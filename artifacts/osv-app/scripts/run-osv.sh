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
# Production userenv is not guaranteed to reach every artifact launch wrapper.
# Supply the checked-in canonical deployment URL only in an actual deployment;
# preview must never inherit it.
DEPLOYED="$(printf '%s' "${REPLIT_DEPLOYMENT:-}" | tr '[:upper:]' '[:lower:]')"
if [[ "${DEPLOYED}" == "1" || "${DEPLOYED}" == "true" ]]; then
  if [[ -z "${LIMITS_MAX_WEBHOOK_URL:-}" ]]; then
    export LIMITS_MAX_WEBHOOK_URL="https://gisuzter.replit.app/limit-notifications/max/webhook"
  fi
  if [[ ! "${LIMITS_MAX_WEBHOOK_URL}" =~ ^https://[^/?#@]+(:[0-9]+)?/[^?#]*$ ]]; then
    echo "ОСВ Дашборд не запущен: LIMITS_MAX_WEBHOOK_URL должен быть полным каноническим HTTPS-адресом без credentials, query или fragment" >&2
    exit 1
  fi
fi
exec python3 -m gunicorn -c gunicorn.conf.py app:app
