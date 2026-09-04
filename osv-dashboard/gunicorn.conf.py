# -*- coding: utf-8 -*-
"""Gunicorn configuration for ОСВ Дашборд.

Single worker + 4 threads keeps the in-memory job store and the IMAP
background thread in one process, while still handling parallel HTTP
requests without blocking.
"""
import os

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
