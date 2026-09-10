---
name: Flask + PostgreSQL startup race condition
description: Keep PostgreSQL retry handling, but never block HTTP readiness on slow App Storage recovery or startup snapshots.
---

# Flask + PostgreSQL startup race on Replit

## The rule
Keep retry handling around synchronous schema/migration work, but never include App Storage restore or startup backup in the HTTP readiness path. Start one background durable-recovery thread after the database is usable, and start IMAP only after that thread finishes.

**Why:** PostgreSQL can lag behind Flask, so retry handling is still required. Separately, additive App Storage restore plus a startup snapshot can take about two minutes even when PostgreSQL is healthy. Running that work in Gunicorn's blocking post-fork hook opens the port but prevents every HTTP response, so Replit marks a healthy application failed.

**How to apply:** A normal startup should log database readiness and answer HTTP within the standard readiness window. Durable recovery may finish later; verify its completion separately before relying on IMAP. Keep CLI/deployment maintenance entrypoints capable of running the full synchronous sequence when readiness is not involved.

## Repeated artifact failure notices

Inspect all managed workflow states before repeating a restart of an already responsive dashboard.

**Why:** Repeated artifact-failure messages have arrived while the same dashboard process continued serving HTTP 200, with other workspace services still marked failed. This does not establish why the notices were generated, but a new dashboard crash must not be inferred from the message alone.

**How to apply:** Check the dashboard PID, recent logs, proxied HTTP and all workflow states. Inspect the actual startup-probe responses of each service before concluding that a workspace interruption is external.

Workflow status is not proof that the previous operating-system process stopped.

**Why:** A later launch failed with address-in-use errors while the previous dashboard and API processes were still serving. Node processes can appear as `MainThread` in the process-name column, so filtering only names containing `node` can falsely suggest they disappeared. A duplicate Vite instance also moved to the next port and appeared running while the proxy still targeted the older instance.

**How to apply:** Match listening socket owners to their command line and working directory before diagnosing a crash or terminating a stale process. Stop the managed workflow first, then gracefully terminate only verified leftover application owners. Do not infer coordinated process termination from workflow labels alone.

## Development readiness differs from published health configuration

Keep an inexpensive successful response at each probed service entry point, not just its configured production health URL.

**Why:** Development startup logs showed probes to the API service root returning 404 despite its prefixed health endpoint and the Flask dashboard being healthy. Production health settings did not prevent that root probe. This was a concrete readiness mismatch; whether it caused coordinated workflow teardown was not independently established.

**How to apply:** Check probe URLs and response statuses in each service's startup logs. Preserve real 404 responses for unknown API routes; readiness aliases must not become a catch-all or require a database connection.
