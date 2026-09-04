---
name: Flask + PostgreSQL startup race condition
description: PostgreSQL sometimes isn't ready when Flask starts in Replit, causing init_db() to fail before the port opens.
---

# Flask + PostgreSQL startup race on Replit

## The rule
Always wrap `init_db()` (or any DB call at startup) in a retry loop with `time.sleep(2)` between attempts (up to 10 tries). Without this, the workflow shows as NOT_STARTED / fails to open a port whenever PostgreSQL lags behind Flask.

**Why:** In the Replit container, `postgresql-16` and the Flask process start concurrently. PostgreSQL and startup migrations can occasionally take well beyond the default 30-second readiness window. Gunicorn may already be listening while its worker is still initializing, so the workflow check fails without an application exception. If a DB call runs before PG is ready, SQLAlchemy can also exit before the application becomes responsive.

**How to apply:** Keep the retry loop below. When logs show Gunicorn listening with no traceback but HTTP still times out, allow a longer workflow readiness window before changing ports or application code; verify the later `DB ready` message and an HTTP 200 response.
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
