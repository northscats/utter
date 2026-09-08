---
name: Python dependencies when publishing
description: Clean publishing must include the dashboard's runtime dependencies in the root Python manifest.
---

Keep the root Python dependency manifest and lockfile complete for the Flask dashboard; its nested requirements file alone is not sufficient for Replit publishing.

**Why:** A clean publishing build succeeded but the Flask service immediately exited because Gunicorn was undeclared in the root manifest. Development-only installed packages can mask this mismatch.

**How to apply:** When adding or changing dashboard dependencies, maintain the root manifest as well as the dashboard requirements used outside Replit. Preserve the publishing dependency preflight. Use the real health endpoint for startup checks, not a routing prefix that can legitimately return 404.