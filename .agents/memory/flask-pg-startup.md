---
name: Flask + PostgreSQL startup race condition
description: Keep PostgreSQL retry handling, but never block HTTP readiness on slow App Storage recovery or startup snapshots.
---

# Flask + PostgreSQL startup race on Replit

## The rule
Keep retry handling around synchronous schema/migration work, but never include App Storage restore or startup backup in the HTTP readiness path. Start one background durable-recovery thread after the database is usable, and start IMAP only after that thread finishes.

**Why:** PostgreSQL can lag behind Flask, so retry handling is still required. Separately, additive App Storage restore plus a startup snapshot can take about two minutes even when PostgreSQL is healthy. Running that work in Gunicorn's blocking post-fork hook opens the port but prevents every HTTP response, so Replit marks a healthy application failed.

**How to apply:** A normal startup should log database readiness and answer HTTP within the standard readiness window. Durable recovery may finish later; verify its completion separately before relying on IMAP. Keep CLI/deployment maintenance entrypoints capable of running the full synchronous sequence when readiness is not involved.
