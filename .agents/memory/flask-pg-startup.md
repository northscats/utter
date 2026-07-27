---
name: Flask + PostgreSQL startup race condition
description: PostgreSQL sometimes isn't ready when Flask starts in Replit, causing init_db() to fail before the port opens.
---

# Flask + PostgreSQL startup race on Replit

## The rule
Always wrap `init_db()` (or any DB call at startup) in a retry loop with `time.sleep(2)` between attempts (up to 10 tries). Without this, the workflow shows as NOT_STARTED / fails to open a port whenever PostgreSQL lags behind Flask.

**Why:** In the Replit container, `postgresql-16` and the Flask process start concurrently. PostgreSQL can take 2–10 s to accept connections. If `db.create_all()` or any migration SQL runs before PG is ready, SQLAlchemy raises a connection error and the process exits before binding to `PORT` — the workflow manager then marks it NOT_STARTED.

**How to apply:** In `app.py` (or equivalent entry point), replace bare `init_db()` call with:
```python
for _attempt in range(10):
    try:
        init_db()
        break
    except Exception as _e:
        print(f'[startup] DB not ready (attempt {_attempt+1}/10): {_e}')
        if _attempt == 9:
            raise
        time.sleep(2)
```
`time` must already be imported (it is in osv-dashboard/app.py).
