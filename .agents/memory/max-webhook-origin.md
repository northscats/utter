---
name: Публичный webhook MAX
description: Безопасный выбор внешнего origin для регистрации MAX webhook в Replit deployment.
---

Адрес MAX webhook должен поступать из проверенных deployment-метаданных и сохраняться как production-переменная окружения. Нельзя строить его из `Host` или proxy-заголовков входящего запроса.

**Why:** Replit завершает TLS до Gunicorn, поэтому WSGI видит HTTP; слепое доверие proxy-заголовкам исправляет схему, но превращает проверочный запрос с webhook-secret в SSRF и позволяет вывести секрет через подменённый Host.

**How to apply:** При смене production-домена заново получить канонический URL через deployment metadata и обновить production `LIMITS_MAX_WEBHOOK_URL`. До любой передачи секрета проверять полный HTTPS URL; preview не наследует production origin.