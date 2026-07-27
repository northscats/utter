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

    Initialises the database and starts the IMAP background thread.
    Retries up to 10 times if PostgreSQL is not yet available.
    """
    import time, sys
    sys.path.insert(0, os.path.dirname(__file__))

    from app import app, init_db, _ensure_imap_thread

    for attempt in range(10):
        try:
            with app.app_context():
                init_db()
            _ensure_imap_thread()
            server.log.info(f'[worker {worker.pid}] DB ready, IMAP thread started')
            return
        except Exception as exc:
            server.log.warning(
                f'[worker {worker.pid}] DB not ready (attempt {attempt + 1}/10): {exc}')
            if attempt == 9:
                raise
            time.sleep(2)
