#!/usr/bin/env bash
# Slim launcher for the LarkMemoryCore competition runtime when only the
# moonshot/kimi-k2.5 backend is needed (Feishu bridge use case).
#
# Skips the HuggingFace daemon entirely (no LoRA adapter required) and
# rewrites models.competition.json so only `moonshot/kimi-k2.5` is registered.
#
# Usage:
#   bash ops/feishu_office_competition_start_kimi.sh
#
# Env overrides (optional):
#   FEISHU_OFFICE_API_PORT        default 18100
#   FEISHU_OFFICE_COMPUTE_PORT    default 19100
#   KIMI_CLI_PATH                 default <repo>/ops/openclaw_kimi_cli.py
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/feishu_office_competition_common.sh"

KIMI_CLI_PATH="${KIMI_CLI_PATH:-${SCRIPT_DIR}/openclaw_kimi_cli.py}"

# 1) Stop only the slim runtime's processes (compute + api).
#    Leaves feishu_bridge and unrelated services alone.
stop_pid_file_if_running "${COMPUTE_PID_FILE}"
stop_pid_file_if_running "${API_PID_FILE}"
stop_port_if_listening "${API_PORT}"
stop_port_if_listening "${COMPUTE_PORT}"

# 2) Write a slim models.competition.json with just kimi-k2.5.
ensure_runtime_api_key >/dev/null
cat > "${MODELS_FILE}" <<EOF
{
  "models": [
    {
      "id": "moonshot/kimi-k2.5",
      "owned_by": "moonshot",
      "created": 1737363858,
      "serving": {
        "api_mode": "both",
        "prompt_style": "buddy_deepseek_r1",
        "default_max_tokens": 256,
        "max_max_tokens": 1024,
        "max_input_chars": 32768,
        "request_timeout_ms": 300000,
        "stream_idle_timeout_s": 120,
        "allow_anonymous_models": false
      },
      "tool": {
        "cli_path": "${KIMI_CLI_PATH}",
        "numactl_nodes": "",
        "taskset_cpus": "",
        "extra_args": ""
      }
    }
  ]
}
EOF

# 3) Generate the env file (reuses common.sh).
write_runtime_env_file

# 4) Sanity check the binaries the slim runtime actually depends on.
if [[ ! -x "${REPO_ROOT}/build/bin/compute_server" ]]; then
  echo "Missing compute_server binary at ${REPO_ROOT}/build/bin/compute_server" >&2
  exit 1
fi
if [[ ! -f "${KIMI_CLI_PATH}" ]]; then
  echo "Missing Kimi bridge CLI at ${KIMI_CLI_PATH}" >&2
  exit 1
fi
chmod +x "${KIMI_CLI_PATH}" 2>/dev/null || true

# 5) Load env, start compute, then api.
set -a
source "${ENV_FILE}"
set +a

nohup "${REPO_ROOT}/build/bin/compute_server" > "${COMPUTE_LOG}" 2>&1 &
echo $! > "${COMPUTE_PID_FILE}"

nohup python3 -m api_server.main > "${API_LOG}" 2>&1 &
echo $! > "${API_PID_FILE}"

# 6) Wait for both health gates.
wait_for_http "http://${API_HOST}:${API_PORT}/health"
wait_for_http "http://${API_HOST}:${API_PORT}/ready"

echo "feishu-office competition runtime (kimi-only) started"
echo "  api     = http://${API_HOST}:${API_PORT}"
echo "  compute = ${COMPUTE_HOST}:${COMPUTE_PORT}"
echo "  models  = ${MODELS_FILE}"
echo "  api log = ${API_LOG}"
echo "  cmp log = ${COMPUTE_LOG}"
