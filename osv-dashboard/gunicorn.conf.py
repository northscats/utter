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
import urllib.request

# ── Process model ──────────────────────────────────────────────────────────
workers = 1          # one process → shared JobStore + single IMAP thread
threads = 4          # 4 threads → I/O concurrency without multiple processes
timeout = 120        # allow slow XLS/ZIP uploads time to save to disk
keepalive = 10

# ── Network ────────────────────────────────────────────────────────────────
bind = '0.0.0.0:' + os.environ.get('PORT', '8080')

# ── Logging ────────────────────────────────────────────────────────────────
accesslog  = '-'
errorlog   = '-'
loglevel   = 'info'

# ── Independent HTTP liveness watchdog ─────────────────────────────────────
health_watchdog_interval = float(
    os.environ.get('HEALTH_WATCHDOG_INTERVAL', '15'))
health_watchdog_timeout = float(
    os.environ.get('HEALTH_WATCHDOG_TIMEOUT', '3'))
health_watchdog_startup_grace = float(
    os.environ.get('HEALTH_WATCHDOG_STARTUP_GRACE', '30'))


def _health_url():
    port = os.environ.get('PORT', '8080')
    return f'http://127.0.0.1:{port}/healthz'


def _probe_health(timeout=None):
    """Return True only for a timely 200 response from the Flask worker."""
    try:
        with urllib.request.urlopen(
                _health_url(),
                timeout=timeout or health_watchdog_timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _terminate_unresponsive_worker(server):
    """Terminate the sole worker and let the Gunicorn master replace it."""
    worker_list = list(server.WORKERS.values())
    if len(worker_list) != 1:
        server.log.error(
            'health watchdog expected exactly one worker, found %s; '
            'automatic recovery skipped', len(worker_list))
        return False
    worker = worker_list[0]
    server.log.error(
        '[worker %s] health probe timed out; terminating for recovery',
        worker.pid)
    # SIGTERM is graceful and can leave a worker waiting on the same stuck
    # threads. SIGABRT is Gunicorn's own worker-timeout signal and makes the
    # master reap and replace the unresponsive process promptly.
    os.kill(worker.pid, signal.SIGABRT)
    return True


def _watchdog_loop(server, stop_event=None):
    """Continuously check real HTTP responsiveness from the master process."""
    stop_event = stop_event or threading.Event()
    if stop_event.wait(health_watchdog_startup_grace):
        return
    while not stop_event.is_set():
        if not _probe_health():
            _terminate_unresponsive_worker(server)
            # Give the master time to reap and replace the worker before the
            # next probe; with one worker this also prevents duplicate IMAP.
            if stop_event.wait(max(health_watchdog_interval, 1)):
                return
            continue
        if stop_event.wait(health_watchdog_interval):
            return


def when_ready(server):
    """Start one watchdog thread in the Gunicorn master process."""
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

    for attempt in range(10):
        try:
            init_db(perform_durable=False)
            server.log.info(f'[worker {worker.pid}] DB ready')
            break
        except Exception as exc:
            server.log.warning(
                f'[worker {worker.pid}] DB not ready (attempt {attempt + 1}/10): {exc}')
            if attempt == 9:
                raise
            time.sleep(2)

    def finish_startup():
        try:
            _finish_durable_startup(restore=True)
            _ensure_imap_thread()
            server.log.info(
                f'[worker {worker.pid}] durable recovery ready, IMAP thread started')
        except Exception:
            server.log.exception(
                f'[worker {worker.pid}] background startup failed')

    threading.Thread(
        target=finish_startup,
        name='durable-startup',
        daemon=True,
    ).start()
