#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage:
  ./ops/openclaw_feishu_buddy_ascend_check.sh \
    --scenario <dm-nonstream|dm-stream|group-at-nonstream|group-at-stream> \
    --trace-token <round1-token> \
    --trace-token <round2-token>

Optional environment variables:
  LARK_MEMORY_CORE_FEISHU_API_BASE_URL      default: http://127.0.0.1:18100
  LARK_MEMORY_CORE_FEISHU_LOG_SINCE         default: 15 minutes ago
  LARK_MEMORY_CORE_FEISHU_REPORT_DIR        default: reports/openclaw-feishu-<timestamp>-<scenario>
  LARK_MEMORY_CORE_FEISHU_API_LOG_PATH      explicit API log file for manual isolated process
  LARK_MEMORY_CORE_FEISHU_COMPUTE_LOG_PATH  explicit compute log file for manual isolated process
  LARK_MEMORY_CORE_FEISHU_API_KEY           bearer token used for /v1/models
  LARK_MEMORY_CORE_FEISHU_API_KEY_FILE      bearer token file used for /v1/models
  LARK_MEMORY_CORE_FEISHU_SKIP_PROMPT=1     skip the interactive "press Enter after sending" prompt

This script does not send Feishu messages. It performs service checks, captures
logs, asserts trace evidence, and writes a summary for the given scenario.
EOF
}

SCENARIO=""
TRACE_TOKENS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      SCENARIO="${2:-}"
      shift 2
      ;;
    --trace-token)
      TRACE_TOKENS+=("${2:-}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${SCENARIO}" ]]; then
  echo "missing --scenario" >&2
  usage >&2
  exit 1
fi

if [[ "${#TRACE_TOKENS[@]}" -ne 2 ]]; then
  echo "exactly two --trace-token arguments are required" >&2
  usage >&2
  exit 1
fi

API_BASE_URL="${LARK_MEMORY_CORE_FEISHU_API_BASE_URL:-http://127.0.0.1:18100}"
LOG_SINCE="${LARK_MEMORY_CORE_FEISHU_LOG_SINCE:-15 minutes ago}"
REPORT_DIR="${LARK_MEMORY_CORE_FEISHU_REPORT_DIR:-${REPO_ROOT}/reports/openclaw-feishu-$(date +%Y%m%d-%H%M%S)-${SCENARIO}}"
API_LOG_PATH="${LARK_MEMORY_CORE_FEISHU_API_LOG_PATH:-}"
COMPUTE_LOG_PATH="${LARK_MEMORY_CORE_FEISHU_COMPUTE_LOG_PATH:-}"
API_KEY="${LARK_MEMORY_CORE_FEISHU_API_KEY:-}"
API_KEY_FILE="${LARK_MEMORY_CORE_FEISHU_API_KEY_FILE:-$HOME/.config/lark-memory-core/credentials/client_api_key.txt}"

mkdir -p "${REPORT_DIR}"

HOST_INFO_PATH="${REPORT_DIR}/00_host_info.txt"
READY_PATH="${REPORT_DIR}/01_ready.json"
HEALTH_PATH="${REPORT_DIR}/02_health.json"
MODELS_PATH="${REPORT_DIR}/03_models.json"
UNITS_PATH="${REPORT_DIR}/04_units.txt"
LOGS_PATH="${REPORT_DIR}/05_logs.jsonl"
SUMMARY_JSON_PATH="${REPORT_DIR}/summary.json"
SUMMARY_MD_PATH="${REPORT_DIR}/summary.md"

if [[ -z "${API_KEY}" && -f "${API_KEY_FILE}" ]]; then
  API_KEY="$(tr -d '\r\n' < "${API_KEY_FILE}")"
fi

AUTH_ARGS=()
if [[ -n "${API_KEY}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${API_KEY}")
fi

{
  echo "date=$(date '+%F %T %Z')"
  echo "host=$(hostname)"
  echo "user=$(whoami)"
  echo "cwd=$(pwd)"
  echo "repo_root=${REPO_ROOT}"
  echo "scenario=${SCENARIO}"
  echo "trace_token_round_1=${TRACE_TOKENS[0]}"
  echo "trace_token_round_2=${TRACE_TOKENS[1]}"
  echo "api_base_url=${API_BASE_URL}"
  echo "log_since=${LOG_SINCE}"
  git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null | sed 's/^/git_head=/'
  git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's/^/git_branch=/'
} > "${HOST_INFO_PATH}"

curl -fsS --max-time 10 "${API_BASE_URL}/health" > "${HEALTH_PATH}"
curl -fsS --max-time 10 "${API_BASE_URL}/ready" > "${READY_PATH}"
curl -fsS --max-time 10 "${AUTH_ARGS[@]}" "${API_BASE_URL}/v1/models" > "${MODELS_PATH}"

if systemctl --user show-environment >/dev/null 2>&1; then
  mapfile -t MANAGED_UNITS < <(lark_memory_core_managed_units)
  if [[ "${#MANAGED_UNITS[@]}" -gt 0 ]]; then
    systemctl --user --no-pager --full status "${MANAGED_UNITS[@]}" > "${UNITS_PATH}" || true
  fi
fi

echo "[info] scenario=${SCENARIO}" >&2
echo "[info] trace_token_round_1=${TRACE_TOKENS[0]}" >&2
echo "[info] trace_token_round_2=${TRACE_TOKENS[1]}" >&2
echo "[info] report_dir=${REPORT_DIR}" >&2

if [[ "${LARK_MEMORY_CORE_FEISHU_SKIP_PROMPT:-0}" != "1" && -t 0 ]]; then
  echo "[action] 请现在在 Feishu 中完成两轮消息发送，然后按回车继续日志核对。" >&2
  read -r _
fi

capture_logs() {
  if [[ -n "${API_LOG_PATH}" || -n "${COMPUTE_LOG_PATH}" ]]; then
    : > "${LOGS_PATH}"
    for path in "${API_LOG_PATH}" "${COMPUTE_LOG_PATH}"; do
      if [[ -n "${path}" && -f "${path}" ]]; then
        cat "${path}" >> "${LOGS_PATH}"
        printf '\n' >> "${LOGS_PATH}"
      fi
    done
    return 0
  fi

  if systemctl --user show-environment >/dev/null 2>&1; then
    mapfile -t MANAGED_UNITS < <(lark_memory_core_managed_units)
    if [[ "${#MANAGED_UNITS[@]}" -gt 0 ]] && journalctl --user -u "${MANAGED_UNITS[0]}" --no-pager -n 1 >/dev/null 2>&1; then
      JOURNAL_ARGS=()
      for unit in "${MANAGED_UNITS[@]}"; do
        JOURNAL_ARGS+=(-u "${unit}")
      done
      journalctl --user "${JOURNAL_ARGS[@]}" --since "${LOG_SINCE}" --no-pager -o cat > "${LOGS_PATH}"
      return 0
    fi
  fi

  mapfile -t RAW_LOG_PATHS < <(lark_memory_core_log_paths)
  : > "${LOGS_PATH}"
  for raw_path in "${RAW_LOG_PATHS[@]}"; do
    path="${raw_path//%h/$HOME}"
    if [[ -f "${path}" ]]; then
      cat "${path}" >> "${LOGS_PATH}"
      printf '\n' >> "${LOGS_PATH}"
    fi
  done
}

capture_logs

TRACE_TOKEN_ROUND_1="${TRACE_TOKENS[0]}" \
TRACE_TOKEN_ROUND_2="${TRACE_TOKENS[1]}" \
SCENARIO_NAME="${SCENARIO}" \
HOST_INFO_PATH="${HOST_INFO_PATH}" \
LOGS_PATH="${LOGS_PATH}" \
SUMMARY_JSON_PATH="${SUMMARY_JSON_PATH}" \
SUMMARY_MD_PATH="${SUMMARY_MD_PATH}" \
"${LARK_MEMORY_CORE_PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

logs_path = Path(os.environ["LOGS_PATH"])
summary_json_path = Path(os.environ["SUMMARY_JSON_PATH"])
summary_md_path = Path(os.environ["SUMMARY_MD_PATH"])
host_info_path = Path(os.environ["HOST_INFO_PATH"])
scenario = os.environ["SCENARIO_NAME"]
token1 = os.environ["TRACE_TOKEN_ROUND_1"]
token2 = os.environ["TRACE_TOKEN_ROUND_2"]

entries = []
for raw_line in logs_path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw_line.strip()
    if not line or not line.startswith("{"):
        continue
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(parsed, dict):
        entries.append(parsed)

raw_candidates = [
    entry for entry in entries
    if entry.get("message") == "API server received raw request"
    and token2 in str(entry.get("raw_request", ""))
]
raw_entry = None
for candidate in raw_candidates:
    raw_request = str(candidate.get("raw_request", ""))
    if token1 in raw_request and token2 in raw_request:
        raw_entry = candidate
        break
if raw_entry is None and raw_candidates:
    raw_entry = raw_candidates[-1]

request_id = raw_entry.get("request_id", "") if raw_entry else ""
api_prompt_entry = next(
    (
        entry for entry in entries
        if entry.get("message") == "API server received prompt"
        and entry.get("request_id") == request_id
    ),
    None,
)
compute_prompt_entry = next(
    (
        entry for entry in entries
        if entry.get("message") == "Compute server received prompt"
        and entry.get("request_id") == request_id
    ),
    None,
)
request_completed_entry = next(
    (
        entry for entry in entries
        if entry.get("message") == "Request completed"
        and entry.get("request_id") == request_id
    ),
    None,
)

raw_request = str(raw_entry.get("raw_request", "")) if raw_entry else ""
api_prompt = str(api_prompt_entry.get("prompt", "")) if api_prompt_entry else ""
compute_prompt = str(compute_prompt_entry.get("prompt", "")) if compute_prompt_entry else ""

checks = {
    "found_raw_request_log": raw_entry is not None,
    "found_api_prompt_log": api_prompt_entry is not None,
    "found_compute_prompt_log": compute_prompt_entry is not None,
    "raw_request_contains_round_1": token1 in raw_request,
    "raw_request_contains_round_2": token2 in raw_request,
    "api_prompt_contains_round_2_only": token2 in api_prompt and token1 not in api_prompt,
    "compute_prompt_matches_api_prompt": bool(api_prompt) and api_prompt == compute_prompt,
    "http_status_is_200": request_completed_entry is not None and int(request_completed_entry.get("status_code", 0)) == 200,
}

summary = {
    "scenario": scenario,
    "trace_token_round_1": token1,
    "trace_token_round_2": token2,
    "request_id": request_id,
    "api_prompt": api_prompt,
    "compute_prompt": compute_prompt,
    "status_code": request_completed_entry.get("status_code") if request_completed_entry else None,
    "checks": checks,
    "passed": all(checks.values()),
}

summary_json_path.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

host_info = host_info_path.read_text(encoding="utf-8", errors="replace").strip()
lines = [
    f"# OpenClaw Feishu Check Summary: {scenario}",
    "",
    "## Host Info",
    "",
    "```text",
    host_info,
    "```",
    "",
    "## Checks",
    "",
]
for key, value in checks.items():
    lines.append(f"- {key}: {'PASS' if value else 'FAIL'}")
lines.extend(
    [
        "",
        "## Key Evidence",
        "",
        f"- request_id: {request_id or '<missing>'}",
        f"- api_prompt: {api_prompt or '<missing>'}",
        f"- compute_prompt: {compute_prompt or '<missing>'}",
        f"- status_code: {summary['status_code']}",
        "",
        f"Overall: {'PASS' if summary['passed'] else 'FAIL'}",
        "",
    ]
)
summary_md_path.write_text("\n".join(lines), encoding="utf-8")

if not summary["passed"]:
    raise SystemExit(1)
PY

echo "[ok] summary written to ${SUMMARY_MD_PATH}" >&2
