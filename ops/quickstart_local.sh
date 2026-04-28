#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
source "${SCRIPT_DIR}/common.sh"
RUN_DIR="${REPO_ROOT}/.run"
COMPUTE_LOG="${RUN_DIR}/quickstart-compute.log"
API_LOG="${RUN_DIR}/quickstart-api.log"
PYTHON_BIN="${LARK_MEMORY_CORE_PYTHON_BIN}"

mkdir -p "${RUN_DIR}"
cd "${REPO_ROOT}"

if [[ ! -f ".env" ]]; then
  echo "[error] missing .env. Copy config.example.env to .env first." >&2
  exit 1
fi

set -a
source ".env"
set +a

validate_active_runtime_config
lark_memory_core_configure_cmake
lark_memory_core_build_targets generate_python_proto compute_server

CURL_HOST="${API_BIND_HOST:-127.0.0.1}"
if [[ "${CURL_HOST}" == "0.0.0.0" ]]; then
  CURL_HOST="127.0.0.1"
fi
API_PORT_VALUE="${API_BIND_PORT:-${API_PORT:-8000}}"
BASE_URL="http://${CURL_HOST}:${API_PORT_VALUE}"

API_PID=""
COMPUTE_PID=""

cleanup() {
  if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" 2>/dev/null; then
    kill -TERM "${API_PID}" 2>/dev/null || true
    for _ in $(seq 1 10); do
      if ! kill -0 "${API_PID}" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    if kill -0 "${API_PID}" 2>/dev/null; then
      kill -KILL "${API_PID}" 2>/dev/null || true
    fi
    wait "${API_PID}" 2>/dev/null || true
  fi
  if [[ -n "${COMPUTE_PID}" ]] && kill -0 "${COMPUTE_PID}" 2>/dev/null; then
    kill -TERM "${COMPUTE_PID}" 2>/dev/null || true
    for _ in $(seq 1 10); do
      if ! kill -0 "${COMPUTE_PID}" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    if kill -0 "${COMPUTE_PID}" 2>/dev/null; then
      kill -KILL "${COMPUTE_PID}" 2>/dev/null || true
    fi
    wait "${COMPUTE_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

show_logs() {
  echo "--- compute log tail ---" >&2
  tail -n 40 "${COMPUTE_LOG}" >&2 || true
  echo "--- api log tail ---" >&2
  tail -n 40 "${API_LOG}" >&2 || true
}

./build/bin/compute_server >"${COMPUTE_LOG}" 2>&1 &
COMPUTE_PID=$!

"${PYTHON_BIN}" -m uvicorn api_server.main:app \
  --host "${API_BIND_HOST:-127.0.0.1}" \
  --port "${API_PORT_VALUE}" >"${API_LOG}" 2>&1 &
API_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 "${BASE_URL}/ready" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS --max-time 5 "${BASE_URL}/ready" >/dev/null 2>&1; then
  echo "[error] API did not become ready at ${BASE_URL}/ready" >&2
  show_logs
  exit 1
fi

AUTH_ARGS=()
CLIENT_API_KEY="${API_KEY:-}"
if [[ -z "${CLIENT_API_KEY}" ]]; then
  CLIENT_KEY_FILE="${HOME}/.config/lark-memory-core/credentials/client_api_key.txt"
  if [[ -f "${CLIENT_KEY_FILE}" ]]; then
    CLIENT_API_KEY="$(tr -d '\r\n' < "${CLIENT_KEY_FILE}")"
  fi
fi
if [[ -n "${CLIENT_API_KEY}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${CLIENT_API_KEY}")
fi

MODELS_JSON=$(curl -fsS --max-time 10 "${AUTH_ARGS[@]}" "${BASE_URL}/v1/models") || {
  echo "[error] failed to list models from ${BASE_URL}/v1/models" >&2
  if [[ -z "${CLIENT_API_KEY}" ]]; then
    echo "[hint] If auth is enabled, set API_KEY in .env or provide ~/.config/lark-memory-core/credentials/client_api_key.txt." >&2
  fi
  show_logs
  exit 1
}

MODEL_ID=$(printf '%s' "${MODELS_JSON}" | "${PYTHON_BIN}" -c 'import json,sys; payload=json.load(sys.stdin); data=payload.get("data", []); print(data[0]["id"] if data else "")')
if [[ -z "${MODEL_ID}" ]]; then
  echo "[error] no models available from /v1/models" >&2
  show_logs
  exit 1
fi

CHAT_BODY=$("${PYTHON_BIN}" - "${MODEL_ID}" <<'PY'
import json
import sys

print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": "Say READY only."}],
    "max_tokens": 16,
}))
PY
)

CHAT_RESPONSE=$(curl -fsS --max-time 120 "${AUTH_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d "${CHAT_BODY}" \
  "${BASE_URL}/v1/chat/completions") || {
  echo "[error] chat completion smoke request failed" >&2
  show_logs
  exit 1
}

echo "[ok] ready: ${BASE_URL}/ready"
echo "[ok] model: ${MODEL_ID}"
echo "[ok] completion response:"
printf '%s\n' "${CHAT_RESPONSE}" | "${PYTHON_BIN}" -m json.tool
echo "[ok] logs:"
echo "  compute: ${COMPUTE_LOG}"
echo "  api: ${API_LOG}"
