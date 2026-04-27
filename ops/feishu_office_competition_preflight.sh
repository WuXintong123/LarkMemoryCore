#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/feishu_office_competition_common.sh"

ensure_runtime_api_key >/dev/null

echo "[check] compute_server binary"
test -x "${REPO_ROOT}/build/bin/compute_server"

echo "[check] training/runtime python"
test -x "${TRAIN_PYTHON}"

echo "[check] adapter directory"
test -d "${ADAPTER_DIR}"

echo "[check] openclaw cli"
OPENCLAW_BIN=$(resolve_openclaw_bin)
test -n "${OPENCLAW_BIN}"
echo "openclaw=${OPENCLAW_BIN}"

echo "[check] runtime models/env templates"
write_runtime_models_file
write_runtime_env_file
test -f "${MODELS_FILE}"
test -f "${ENV_FILE}"

if [[ -f "${API_PID_FILE}" ]]; then
  echo "[check] api health"
  curl -fsS --max-time 5 "http://${API_HOST}:${API_PORT}/health" >/dev/null
  echo "[check] admin metrics idle"
  curl -fsS --max-time 5 -H "Authorization: Bearer $(read_runtime_api_key)" \
    "http://${API_HOST}:${API_PORT}/v1/admin/metrics" | \
    python3 -c 'import json,sys; payload=json.load(sys.stdin); assert payload["active_compute_slots"] == 0, payload'
fi

if [[ -f "${DAEMON_PID_FILE}" ]]; then
  echo "[check] daemon ping"
  "${TRAIN_PYTHON}" "${COMP_ROOT}/runtime/feishu_office_hf_cli.py" --daemon-port "${DAEMON_PORT}" --ping >/dev/null
fi

echo "preflight ok"

