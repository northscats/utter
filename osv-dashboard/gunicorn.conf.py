# -*- coding: utf-8 -*-
"""Gunicorn configuration for ОСВ Дашборд.

Single worker + 4 threads keeps the in-memory job store and the IMAP
background thread in one process, while still handling parallel HTTP
requests without blocking.

The master process independently probes ``/healthz``. If every HTTP thread is
stuck and the probe times out, the master terminates that one worker; Gunicorn
then starts a replacement. Because ``workers`` remains exactly one, the old
process (including its IMAP/background threads) exits before a replacement is
forked, so recovery cannot create parallel in-process consumers.
"""
import os
import signal
import threading
import time
import json
import urllib.request
import uuid
from datetime import datetime, timezone

# ── Process model ──────────────────────────────────────────────────────────
workers = 1          # one process → shared JobStore + single IMAP thread
threads = 4          # 4 threads → I/O concurrency without multiple processes
timeout = 120        # allow slow XLS/ZIP uploads time to save to disk
keepalive = 10

# ── Network ────────────────────────────────────────────────────────────────
bind = '0.0.0.0:' + os.environ.get('PORT', '8080')
# The 1C receiver validates the immediate proxy peer itself. Do not let
# Gunicorn turn an unverified forwarded header into a trusted WSGI TLS scheme.
forwarded_allow_ips = ''

# ── Logging ────────────────────────────────────────────────────────────────
accesslog  = '-'
errorlog   = '-'
loglevel   = 'info'
# No query strings, Referer, Authorization or request bodies in access logs.
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s'

# ── Independent HTTP liveness watchdog ─────────────────────────────────────
health_watchdog_interval = float(
    os.environ.get('HEALTH_WATCHDOG_INTERVAL', '15'))
health_watchdog_timeout = float(
    os.environ.get('HEALTH_WATCHDOG_TIMEOUT', '3'))
health_watchdog_startup_grace = float(
    os.environ.get('HEALTH_WATCHDOG_STARTUP_GRACE', '30'))
health_watchdog_max_startup_seconds = float(
    os.environ.get('HEALTH_WATCHDOG_MAX_STARTUP_SECONDS', '600'))
health_watchdog_event_spool = os.environ.get(
    'HEALTH_WATCHDOG_EVENT_SPOOL',
    f'/tmp/osv-watchdog-recoveries-{os.environ.get("PORT", "8080")}.json',
)
health_watchdog_state_dir = os.environ.get(
    'HEALTH_WATCHDOG_STATE_DIR', '/tmp')
_pending_recovery_events = []
_recovery_worker_pid = None
_recovery_lock = threading.Lock()


def _health_url():
    port = os.environ.get('PORT', '8080')
    return f'http://127.0.0.1:{port}/healthz'


def _startup_state_path(worker_pid):
    return os.path.join(
        health_watchdog_state_dir, f'osv-startup-{int(worker_pid)}.json')


def _write_startup_state(worker_pid, status):
    """Publish worker bootstrap state to the Gunicorn master.

    The master and worker do not share Python memory after fork.  A small
    atomic file lets the watchdog distinguish an intentional long restore from
    a genuinely wedged worker without weakening recovery forever.
    """
    path = _startup_state_path(worker_pid)
    temporary = f'{path}.tmp'
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary, 'w', encoding='utf-8') as state:
            json.dump({
                'pid': int(worker_pid),
                'status': status,
                'updated_at': time.time(),
            }, state)
            state.flush()
            os.fsync(state.fileno())
        os.replace(temporary, path)
    except Exception:
        # Observability must never stop Gunicorn from starting or replacing a
        # worker.  The watchdog falls back to its ordinary probe in this case.
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_startup_state(worker_pid):
    try:
        with open(_startup_state_path(worker_pid), encoding='utf-8') as state:
            payload = json.load(state)
        if (payload.get('pid') != int(worker_pid)
                or payload.get('status') not in ('starting', 'ready', 'failed')):
            return None
        return payload
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None


def _worker_is_starting(server, worker_pid):
    """Return whether a worker is within its bounded restore window."""
    state = _read_startup_state(worker_pid)
    if not state or state.get('status') != 'starting':
        return False
    try:
        age = max(0.0, time.time() - float(state['updated_at']))
    except (KeyError, TypeError, ValueError):
        return False
    if age < health_watchdog_max_startup_seconds:
        return True
    server.log.error(
        '[worker %s] startup exceeded max grace of %ss',
        worker_pid, health_watchdog_max_startup_seconds)
    return False


def _probe_health(timeout=None):
    """Return True only for a timely 200 response from the Flask worker."""
    try:
        with urllib.request.urlopen(
                _health_url(),
                timeout=timeout or health_watchdog_timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _persist_pending_recovery_events():
    """Atomically persist undelivered events across Gunicorn master restarts."""
    temp_path = f'{health_watchdog_event_spool}.tmp'
    with open(temp_path, 'w', encoding='utf-8') as spool:
        json.dump(_pending_recovery_events, spool, ensure_ascii=False)
        spool.flush()
        os.fsync(spool.fileno())
    os.replace(temp_path, health_watchdog_event_spool)


def _restore_pending_recovery_events(server):
    try:
        with open(health_watchdog_event_spool, encoding='utf-8') as spool:
            events = json.load(spool)
        if isinstance(events, list):
            _pending_recovery_events[:] = [
                event for event in events
                if isinstance(event, dict)
                and event.get('event_id')
                and event.get('occurred_at')
                and isinstance(event.get('worker_pid'), int)
            ]
    except FileNotFoundError:
        return
    except Exception:
        server.log.exception(
            'health watchdog could not restore pending recovery events')


def _deliver_recovery_events(server):
    """Persist queued recovery events through the responsive Flask worker."""
    while _pending_recovery_events:
        event = _pending_recovery_events[0]
        request = urllib.request.Request(
            f'http://127.0.0.1:{os.environ.get("PORT", "8080")}'
            '/healthz/watchdog-recovery',
            data=json.dumps(event).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'X-Watchdog-Event': '1',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(
                    request, timeout=health_watchdog_timeout) as response:
                if response.status != 204:
                    return False
        except Exception:
            return False
        _pending_recovery_events.pop(0)
        try:
            _persist_pending_recovery_events()
        except Exception:
            # The database has already accepted the event. A stale spool may
            # cause a harmless retry after restart; the endpoint deduplicates it.
            server.log.exception(
                'health watchdog could not update recovery event spool')
        server.log.info(
            'health watchdog recovery event persisted for worker %s',
            event['worker_pid'])
    return True


def _terminate_unresponsive_worker(server):
    """Terminate the sole worker and let the Gunicorn master replace it."""
    global _recovery_worker_pid
    worker_list = list(server.WORKERS.values())
    if len(worker_list) != 1:
        server.log.error(
            'health watchdog expected exactly one worker, found %s; '
            'automatic recovery skipped', len(worker_list))
        return False
    worker = worker_list[0]
    with _recovery_lock:
        if _recovery_worker_pid == worker.pid:
            # The master may need several probe intervals to reap a worker
            # after SIGABRT. Do not signal it again or enqueue duplicate
            # recovery events while that replacement is still in progress.
            return False
        # A different PID means Gunicorn has installed the replacement. Allow
        # that worker to be recovered independently if it also becomes stuck.
        _recovery_worker_pid = worker.pid
        server.log.error(
            '[worker %s] health probe timed out; terminating for recovery',
            worker.pid)
        event = {
            'event_id': str(uuid.uuid4()),
            'occurred_at': datetime.now(timezone.utc).isoformat(),
            'worker_pid': worker.pid,
        }
        _pending_recovery_events.append(event)
        try:
            _persist_pending_recovery_events()
        except Exception:
            # Observability must never disable the recovery itself. The event
            # remains queued in master memory and delivery will still be retried.
            server.log.exception(
                'health watchdog could not persist queued recovery event')
        try:
            # SIGTERM is graceful and can leave a worker waiting on the same
            # stuck threads. SIGABRT is Gunicorn's own worker-timeout signal
            # and makes the master reap and replace it promptly.
            os.kill(worker.pid, signal.SIGABRT)
        except Exception:
            _pending_recovery_events.remove(event)
            _recovery_worker_pid = None
            try:
                _persist_pending_recovery_events()
            except Exception:
                server.log.exception(
                    'health watchdog could not roll back recovery event spool')
            raise
    return True


def _watchdog_loop(server, stop_event=None):
    """Continuously check real HTTP responsiveness from the master process."""
    stop_event = stop_event or threading.Event()
    initial_worker_list = list(server.WORKERS.values())
    last_worker_pid = (
        initial_worker_list[0].pid
        if len(initial_worker_list) == 1
        else None
    )
    if stop_event.wait(health_watchdog_startup_grace):
        return
    startup_logged_pid = None
    while not stop_event.is_set():
        worker_list = list(server.WORKERS.values())
        if len(worker_list) == 1:
            worker_pid = worker_list[0].pid
            if last_worker_pid != worker_pid:
                # The replacement runs post_fork schema work before it can
                # answer /healthz. Give every new PID the same startup grace
                # as the initial worker, not only the original master start.
                last_worker_pid = worker_pid
                if stop_event.wait(health_watchdog_startup_grace):
                    return
                continue
            if _worker_is_starting(server, worker_pid):
                if startup_logged_pid != worker_pid:
                    server.log.info(
                        '[worker %s] health probes paused during bounded '
                        'durable startup (max %ss)',
                        worker_pid, health_watchdog_max_startup_seconds)
                    startup_logged_pid = worker_pid
                if stop_event.wait(max(health_watchdog_interval, 1)):
                    return
                continue
        if not _probe_health():
            _terminate_unresponsive_worker(server)
            # Give the master time to reap and replace the worker before the
            # next probe; with one worker this also prevents duplicate IMAP.
            if stop_event.wait(max(health_watchdog_interval, 1)):
                return
            continue
        if _pending_recovery_events:
            _deliver_recovery_events(server)
        if stop_event.wait(health_watchdog_interval):
            return


def when_ready(server):
    """Start one watchdog thread in the Gunicorn master process."""
    _restore_pending_recovery_events(server)
    thread = threading.Thread(
        target=_watchdog_loop,
        args=(server,),
        name='http-health-watchdog',
        daemon=True,
    )
    thread.start()
    server.log.info(
        'HTTP health watchdog started: interval=%ss timeout=%ss grace=%ss',
        health_watchdog_interval,
        health_watchdog_timeout,
        health_watchdog_startup_grace,
    )


def post_fork(server, worker):
    """Called in each worker process after forking from the master.

    Runs schema work before serving, but keeps slow App Storage restore/backup
    out of the readiness path. IMAP starts only after durable recovery ends.
    """
    import threading, time, sys
    sys.path.insert(0, os.path.dirname(__file__))

    from app import app, init_db, _finish_durable_startup, _ensure_imap_thread

    _write_startup_state(worker.pid, 'starting')
    for attempt in range(10):
        try:
            init_db(perform_durable=False)
            server.log.info(f'[worker {worker.pid}] DB ready')
            break
        except Exception as exc:
            server.log.warning(
                f'[worker {worker.pid}] DB not ready (attempt {attempt + 1}/10): {exc}')
            if attempt == 9:
                _write_startup_state(worker.pid, 'failed')
                raise
            time.sleep(2)

    def finish_startup():
        try:
            deployed = os.environ.get('REPLIT_DEPLOYMENT', '').strip().lower()
            restore = deployed in ('1', 'true')
            _finish_durable_startup(restore=restore)
            _write_startup_state(worker.pid, 'ready')
            _ensure_imap_thread()
            server.log.info(
                f'[worker {worker.pid}] durable startup ready '
                f'(restore={"enabled" if restore else "development-skip"}), '
                'IMAP thread started')
        except Exception:
            _write_startup_state(worker.pid, 'failed')
            server.log.exception(
                f'[worker {worker.pid}] background startup failed')

    threading.Thread(
        target=finish_startup,
        name='durable-startup',
        daemon=True,
    ).start()
