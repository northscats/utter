#!/bin/sh
set -e

echo "[entrypoint] Ожидание PostgreSQL..."
until python -c "
import os, psycopg2
try:
    psycopg2.connect(os.environ['DATABASE_URL']).close()
    print('[entrypoint] PostgreSQL готов')
except Exception as e:
    raise SystemExit(1)
" 2>/dev/null; do
  sleep 1
done

echo "[entrypoint] Инициализация базы данных..."
python - <<'PY'
from app import app, init_db
with app.app_context():
    init_db()
PY

echo "[entrypoint] Запуск gunicorn на порту ${PORT:-8080}..."
exec gunicorn \
  --bind "0.0.0.0:${PORT:-8080}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  app:app
