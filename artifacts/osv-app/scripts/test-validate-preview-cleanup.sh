#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-$(
  python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)}"
TMP_DIR="$(mktemp -d -t osv-preview-cleanup-test.XXXXXX)"
PID_FILE="${TMP_DIR}/server.pid"
VALIDATION_LOG="${TMP_DIR}/validation.log"
RESTART_LOG="${TMP_DIR}/restart.log"
RESTART_PID=""

cleanup() {
  if [[ -n "${RESTART_PID}" ]] && kill -0 "${RESTART_PID}" 2>/dev/null; then
    kill -TERM "${RESTART_PID}" 2>/dev/null || true
    wait "${RESTART_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT INT TERM

echo "Негативная smoke-проверка ОСВ на порту ${PORT}"
if REPLIT_DEV_DOMAIN="negative-smoke.invalid" \
  PORT="${PORT}" \
  STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-45}" \
  PREVIEW_URL="http://127.0.0.1:${PORT}/expected-route-error/" \
  SERVER_PID_FILE="${PID_FILE}" \
  bash "${SCRIPT_DIR}/validate-preview.sh" >"${VALIDATION_LOG}" 2>&1
then
  echo "ОШИБКА негативной проверки: ошибочный маршрут неожиданно прошёл smoke-проверку" >&2
  cat "${VALIDATION_LOG}" >&2
  exit 1
fi

if ! grep -Fq "ОШИБКА preview ОСВ: http://127.0.0.1:${PORT}/expected-route-error/healthz:" "${VALIDATION_LOG}"; then
  echo "ОШИБКА негативной проверки: получена не ожидаемая ошибка маршрута" >&2
  cat "${VALIDATION_LOG}" >&2
  exit 1
fi
echo "OK: получена ожидаемая ошибка маршрута"

if [[ ! -s "${PID_FILE}" ]]; then
  echo "ОШИБКА очистки preview ОСВ: PID запущенного gunicorn не был записан" >&2
  exit 1
fi
SERVER_PID="$(cat "${PID_FILE}")"
if kill -0 "${SERVER_PID}" 2>/dev/null; then
  echo "ОШИБКА очистки preview ОСВ: gunicorn ${SERVER_PID} остался запущен после ошибки smoke-проверки" >&2
  exit 1
fi
echo "OK: gunicorn ${SERVER_PID} остановлен"

(
  cd "${SCRIPT_DIR}/.."
  PORT="${PORT}" BASE_PATH="/" exec bash scripts/run-osv.sh
) >"${RESTART_LOG}" 2>&1 &
RESTART_PID=$!

deadline=$((SECONDS + 20))
while (( SECONDS < deadline )); do
  if ! kill -0 "${RESTART_PID}" 2>/dev/null; then
    echo "ОШИБКА очистки preview ОСВ: повторный запуск не смог занять порт ${PORT}" >&2
    cat "${RESTART_LOG}" >&2
    exit 1
  fi
  if python3 - "${PORT}" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=1
    ) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    echo "OK: порт ${PORT} немедленно занят повторным запуском"
    exit 0
  fi
  sleep 0.2
done

echo "ОШИБКА очистки preview ОСВ: повторный запуск не ответил на порту ${PORT}" >&2
cat "${RESTART_LOG}" >&2
exit 1