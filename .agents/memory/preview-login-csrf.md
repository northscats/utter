---
name: Preview login CSRF
description: Replit preview can scope the login GET and POST session cookies differently.
---

The login form uses its own short-lived signed stateless token plus a strict Origin/Referer host check because the Replit preview proxy can issue the form GET and credential POST with different session-cookie scopes. Authenticated state-changing routes remain protected by session-bound Flask-WTF CSRF.

**Why:** The preview returned 403 before reaching credential validation even when the form contained a fresh token, while direct local requests worked.

**How to apply:** If preview login starts returning a generic 403 again, check forwarded host handling and the login token boundary first; never leave login unprotected and do not weaken CSRF on authenticated actions.