# -*- coding: utf-8 -*-
"""Thread-safe in-memory job store for background upload tasks.

Each job lives for 2 hours then is automatically evicted on the next
create_job() call. With a single gunicorn worker this is safe; with
multiple workers you would need shared state (Redis, DB).
"""
import threading
import uuid
from datetime import datetime

__all__ = ['create_job', 'get_job', 'finish_job', 'fail_job']

_lock: threading.Lock = threading.Lock()
_jobs: dict = {}   # job_id -> {status, result, error, created_at, company_id}


def create_job(company_id: int, filenames=None) -> str:
    """Register a new job and return its UUID string."""
    job_id = str(uuid.uuid4())
    files = [
        str(name).strip()
        for name in (filenames or [])
        if str(name).strip()
    ]
    with _lock:
        _evict_stale()
        _jobs[job_id] = {
            'status': 'processing',
            'result': None,
            'error': None,
            'created_at': datetime.utcnow(),
            'company_id': company_id,
            'files': files,
        }
    return job_id


def get_job(job_id: str) -> dict | None:
    """Return a shallow copy of the job dict, or None if not found / expired."""
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def finish_job(job_id: str, result: dict) -> None:
    """Mark the job as done and store its result payload."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(status='done', result=result)


def fail_job(job_id: str, error: str) -> None:
    """Mark the job as failed with an error message."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(status='error', error=error)


def _evict_stale() -> None:
    """Remove jobs older than 2 hours. Caller must hold _lock."""
    now = datetime.utcnow()
    stale = [jid for jid, j in _jobs.items()
             if (now - j['created_at']).total_seconds() > 7200]
    for jid in stale:
        del _jobs[jid]
