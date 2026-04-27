#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000}"
MODEL_ID="${MODEL_ID:-deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B}"
API_KEY_VALUE="${API_KEY:-}"
PROMPT="${1:-Say READY only.}"
MAX_TOKENS="${MAX_TOKENS:-16}"

AUTH_ARGS=()
if [[ -n "${API_KEY_VALUE}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${API_KEY_VALUE}")
fi

curl -fsS "${AUTH_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d "$(python3 - "${MODEL_ID}" "${PROMPT}" "${MAX_TOKENS}" <<'PY'
import json
import sys

print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": sys.argv[2]}],
    "max_tokens": int(sys.argv[3]),
}, ensure_ascii=False))
PY
)" \
  "${BASE_URL}/v1/chat/completions"
