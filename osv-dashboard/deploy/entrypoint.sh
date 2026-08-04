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
# ВАЖНО: строго один worker (задан в gunicorn.conf.py).
# Архитектура опирается на внутрипроцессные фоновые потоки и семафор
# (IMAP-цикл, обработка пакетов документов) — несколько worker'ов
# нарушили бы сериализацию обработки и видимость статусов.
exec gunicorn -c gunicorn.conf.py app:app
