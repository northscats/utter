#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PORT="${PORT:-21844}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-45}"
LOG_FILE="$(mktemp -t osv-preview-validation.XXXXXX.log)"
SERVER_PID=""
SERVER_PID_FILE="${SERVER_PID_FILE:-}"

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill -TERM "${SERVER_PID}" 2>/dev/null || true
    for _ in {1..50}; do
      kill -0 "${SERVER_PID}" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "${SERVER_PID}" 2>/dev/null; then
      kill -KILL "${SERVER_PID}" 2>/dev/null || true
    fi
    wait "${SERVER_PID}" 2>/dev/null || true
  fi

  rm -f "${LOG_FILE}"
  exit "${exit_code}"
}
trap cleanup EXIT INT TERM

if [[ -z "${REPLIT_DEV_DOMAIN:-}" ]]; then
  echo "ОШИБКА preview ОСВ: REPLIT_DEV_DOMAIN не задан; невозможно проверить маршрутизированный preview" >&2
  exit 1
fi

echo "Запуск ОСВ на назначенном порту ${PORT}"
(
  cd "${ARTIFACT_DIR}"
  PORT="${PORT}" BASE_PATH="/" exec bash scripts/run-osv.sh
) >"${LOG_FILE}" 2>&1 &
SERVER_PID=$!
if [[ -n "${SERVER_PID_FILE}" ]]; then
  printf '%s\n' "${SERVER_PID}" >"${SERVER_PID_FILE}"
fi

LOCAL_HEALTH_URL="http://127.0.0.1:${PORT}/healthz"
deadline=$((SECONDS + STARTUP_TIMEOUT))
while (( SECONDS < deadline )); do
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "ОШИБКА preview ОСВ: процесс завершился до готовности ${LOCAL_HEALTH_URL}" >&2
    cat "${LOG_FILE}" >&2
    exit 1
  fi

  if python3 - "${LOCAL_HEALTH_URL}" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=1) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    break
  fi
  sleep 0.5
done

if ! python3 - "${LOCAL_HEALTH_URL}" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
then
  echo "ОШИБКА preview ОСВ: сервис не ответил на ${LOCAL_HEALTH_URL} за ${STARTUP_TIMEOUT} секунд" >&2
  cat "${LOG_FILE}" >&2
  exit 1
fi

PREVIEW_URL="${PREVIEW_URL:-https://${REPLIT_DEV_DOMAIN}/}"
echo "Проверка маршрутизированного preview ${PREVIEW_URL}"
python3 "${SCRIPT_DIR}/check-preview.py" "${PREVIEW_URL}"
