#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMP_ROOT="${REPO_ROOT}/competition/feishu_office"
RUN_ROOT="${REPO_ROOT}/.run/feishu-office-competition"
LOG_ROOT="${RUN_ROOT}/logs"
PID_ROOT="${RUN_ROOT}/pids"
RUNTIME_ROOT="${RUN_ROOT}/runtime"
MEMORY_ROOT="${RUN_ROOT}/memory"

API_PORT="${FEISHU_OFFICE_API_PORT:-18100}"
COMPUTE_PORT="${FEISHU_OFFICE_COMPUTE_PORT:-19100}"
DAEMON_PORT="${FEISHU_OFFICE_DAEMON_PORT:-19600}"
API_HOST="${FEISHU_OFFICE_API_HOST:-127.0.0.1}"
COMPUTE_HOST="${FEISHU_OFFICE_COMPUTE_HOST:-0.0.0.0}"

BASE_MODEL_ID="${FEISHU_OFFICE_BASE_MODEL_ID:-deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B}"
TUNED_MODEL_ID="${FEISHU_OFFICE_TUNED_MODEL_ID:-lark-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice}"
BASE_MODEL_PATH="${FEISHU_OFFICE_BASE_MODEL_PATH:-/home/huangyiheng/buddy-mlir/build/bin/buddy-deepseek-r1-cli}"
TRAIN_PYTHON="${FEISHU_OFFICE_TRAIN_PYTHON:-${HOME}/.venvs/lark-memory-feishu-office/bin/python}"
ADAPTER_DIR="${FEISHU_OFFICE_ADAPTER_DIR:-${COMP_ROOT}/artifacts/adapter}"

API_LOG="${LOG_ROOT}/api.log"
COMPUTE_LOG="${LOG_ROOT}/compute.log"
DAEMON_LOG="${LOG_ROOT}/daemon.log"
API_PID_FILE="${PID_ROOT}/api.pid"
COMPUTE_PID_FILE="${PID_ROOT}/compute.pid"
DAEMON_PID_FILE="${PID_ROOT}/daemon.pid"
ENV_FILE="${RUNTIME_ROOT}/competition.env"
MODELS_FILE="${RUNTIME_ROOT}/models.competition.json"
API_KEY_FILE="${RUNTIME_ROOT}/api_key.txt"

mkdir -p "${LOG_ROOT}" "${PID_ROOT}" "${RUNTIME_ROOT}" "${MEMORY_ROOT}"

resolve_openclaw_bin() {
  bash -lc 'source ~/.nvm/nvm.sh >/dev/null 2>&1 && command -v openclaw'
}

read_openclaw_env_var() {
  local name="$1"
  local value
  value=$(awk -F= -v key="${name}" '$1 == key {print substr($0, index($0, "=") + 1)}' "${HOME}/.openclaw/.env" 2>/dev/null || true)
  printf '%s' "${value}"
}

read_runtime_api_key() {
  if [[ -f "${API_KEY_FILE}" ]]; then
    tr -d '\r\n' < "${API_KEY_FILE}"
    return 0
  fi
  local value
  value=$(read_openclaw_env_var "LARK_MEMORY_CORE_API_KEY")
  if [[ -n "${value}" ]]; then
    printf '%s' "${value}"
    return 0
  fi
  return 1
}

ensure_runtime_api_key() {
  local value
  value=$(read_runtime_api_key)
  if [[ -z "${value}" ]]; then
    echo "Unable to resolve LARK_MEMORY_CORE_API_KEY from ${API_KEY_FILE} or ~/.openclaw/.env" >&2
    return 1
  fi
  printf '%s' "${value}" > "${API_KEY_FILE}"
}

write_runtime_models_file() {
  ensure_runtime_api_key >/dev/null
  cat > "${MODELS_FILE}" <<EOF
{
  "models": [
    {
      "id": "${BASE_MODEL_ID}",
      "owned_by": "deepseek-ai",
      "created": 1737363858,
      "serving": {
        "api_mode": "both",
        "prompt_style": "buddy_deepseek_r1",
        "default_max_tokens": 64,
        "max_max_tokens": 256,
        "max_input_chars": 32768,
        "request_timeout_ms": 300000,
        "stream_idle_timeout_s": 120,
        "allow_anonymous_models": false
      },
      "tool": {
        "cli_path": "${BASE_MODEL_PATH}",
        "numactl_nodes": "",
        "taskset_cpus": "",
        "extra_args": "--no-stats"
      }
    },
    {
      "id": "${TUNED_MODEL_ID}",
      "owned_by": "lark-office",
      "created": 1776441600,
      "serving": {
        "api_mode": "both",
        "prompt_style": "buddy_deepseek_r1",
        "default_max_tokens": 128,
        "max_max_tokens": 256,
        "max_input_chars": 32768,
        "request_timeout_ms": 300000,
        "stream_idle_timeout_s": 120,
        "allow_anonymous_models": false
      },
      "tool": {
        "cli_path": "${TRAIN_PYTHON}",
        "numactl_nodes": "",
        "taskset_cpus": "",
        "extra_args": "${COMP_ROOT}/runtime/feishu_office_hf_cli.py --daemon-host 127.0.0.1 --daemon-port ${DAEMON_PORT} --timeout-s 180"
      }
    }
  ]
}
EOF
}

write_runtime_env_file() {
  ensure_runtime_api_key >/dev/null
  cat > "${ENV_FILE}" <<EOF
GRPC_SERVER_ADDRESS=127.0.0.1:${COMPUTE_PORT}
GRPC_TIMEOUT=600
API_BIND_HOST=${API_HOST}
API_BIND_PORT=${API_PORT}
API_KEY=$(tr -d '\r\n' < "${API_KEY_FILE}")
API_KEY_ID=default
API_KEY_SCOPES=models:read,inference,admin
MODELS_CONFIG_FILE=${MODELS_FILE}
COMPLETION_PROMPT_LIST_CONCURRENCY=1
COMPUTE_SERVER_ADDRESS=${COMPUTE_HOST}:${COMPUTE_PORT}
MAX_COMPUTE_CONCURRENCY=1
COMPUTE_QUEUE_TIMEOUT_MS=0
MAX_QUEUED_REQUESTS=0
STREAM_IDLE_TIMEOUT_S=120
NON_STREAM_IDLE_TIMEOUT_S=12
NON_STREAM_MAX_EXECUTION_S=240
LARK_MEMORY_CORE_DEBUG_PROMPT_IO=1
LARK_MEMORY_CORE_MEMORY_ENGINE_ENABLED=1
LARK_MEMORY_CORE_MEMORY_DB_PATH=${MEMORY_ROOT}/decision_memory.sqlite3
LARK_MEMORY_CORE_MEMORY_MAX_CARDS=3
CLUSTER_CONFIG_FILE=
LOG_LEVEL=INFO
EOF
}

stop_pid_file_if_running() {
  local pid_file="$1"
  if [[ -f "${pid_file}" ]]; then
    local pid
    pid=$(cat "${pid_file}")
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
      sleep 1
      if kill -0 "${pid}" >/dev/null 2>&1; then
        kill -9 "${pid}" >/dev/null 2>&1 || true
      fi
    fi
    rm -f "${pid_file}"
  fi
}

stop_port_if_listening() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    mapfile -t pids < <(lsof -ti tcp:"${port}" || true)
    for pid in "${pids[@]}"; do
      if [[ -n "${pid}" ]]; then
        kill "${pid}" >/dev/null 2>&1 || true
        sleep 1
        kill -9 "${pid}" >/dev/null 2>&1 || true
      fi
    done
  fi
}

wait_for_http() {
  local url="$1"
  local headers=("${@:2}")
  local attempts=30
  while (( attempts > 0 )); do
    if curl -fsS --max-time 5 "${headers[@]}" "${url}" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  return 1
}
