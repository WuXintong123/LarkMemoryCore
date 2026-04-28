#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/feishu_office_competition_common.sh"

"${SCRIPT_DIR}/feishu_office_competition_stop.sh" >/dev/null 2>&1 || true

write_runtime_models_file
write_runtime_env_file

if [[ ! -x "${REPO_ROOT}/build/bin/compute_server" ]]; then
  echo "Missing compute_server binary at ${REPO_ROOT}/build/bin/compute_server" >&2
  exit 1
fi
if [[ ! -x "${TRAIN_PYTHON}" ]]; then
  echo "Missing training/runtime python at ${TRAIN_PYTHON}" >&2
  exit 1
fi
if [[ ! -d "${ADAPTER_DIR}" ]]; then
  echo "Missing adapter directory at ${ADAPTER_DIR}" >&2
  exit 1
fi

nohup "${TRAIN_PYTHON}" "${COMP_ROOT}/runtime/feishu_office_hf_daemon.py" \
  --base-model "${BASE_MODEL_ID}" \
  --adapter-path "${ADAPTER_DIR}" \
  --host 127.0.0.1 \
  --port "${DAEMON_PORT}" \
  --max-input-chars 32768 \
  --default-max-tokens 128 \
  > "${DAEMON_LOG}" 2>&1 &
echo $! > "${DAEMON_PID_FILE}"

sleep 3
"${TRAIN_PYTHON}" "${COMP_ROOT}/runtime/feishu_office_hf_cli.py" --daemon-port "${DAEMON_PORT}" --ping >/dev/null

set -a
source "${ENV_FILE}"
set +a

nohup "${REPO_ROOT}/build/bin/compute_server" > "${COMPUTE_LOG}" 2>&1 &
echo $! > "${COMPUTE_PID_FILE}"

nohup python3 -m api_server.main > "${API_LOG}" 2>&1 &
echo $! > "${API_PID_FILE}"

wait_for_http "http://${API_HOST}:${API_PORT}/health"
wait_for_http "http://${API_HOST}:${API_PORT}/ready"

OPENCLAW_BIN=$(resolve_openclaw_bin)
if [[ -n "${OPENCLAW_BIN}" ]]; then
  export LARK_MEMORY_CORE_API_KEY
  export OPENCLAW_GATEWAY_TOKEN
  LARK_MEMORY_CORE_API_KEY=$(read_runtime_api_key)
  OPENCLAW_GATEWAY_TOKEN=$(read_openclaw_env_var "OPENCLAW_GATEWAY_TOKEN")
  bash -lc "source ~/.nvm/nvm.sh >/dev/null 2>&1 && export LARK_MEMORY_CORE_API_KEY='${LARK_MEMORY_CORE_API_KEY}' OPENCLAW_GATEWAY_TOKEN='${OPENCLAW_GATEWAY_TOKEN}' && openclaw gateway restart >/dev/null && openclaw models list >/dev/null"
fi

echo "feishu-office competition runtime started"
echo "api=http://${API_HOST}:${API_PORT}"
echo "compute=${COMPUTE_HOST}:${COMPUTE_PORT}"
echo "daemon=127.0.0.1:${DAEMON_PORT}"

