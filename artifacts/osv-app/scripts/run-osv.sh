#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
APP_DIR="${WORKSPACE_DIR}/osv-dashboard"

if [[ ! -f "${APP_DIR}/app.py" || ! -f "${APP_DIR}/gunicorn.conf.py" ]]; then
  echo "ОСВ Дашборд не запущен: Flask-приложение не найдено в ${APP_DIR}" >&2
  exit 1
fi

if [[ -z "${PORT:-}" ]]; then
  echo "ОСВ Дашборд не запущен: переменная PORT не задана workflow артефакта" >&2
  exit 1
fi

cd "${APP_DIR}"
exec python3 -m gunicorn -c gunicorn.conf.py app:app