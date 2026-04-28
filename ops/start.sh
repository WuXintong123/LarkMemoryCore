#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/common.sh"
require_systemd_user
require_repo_env
cd "${REPO_ROOT}"
set -a
source "${REPO_ROOT}/.env"
set +a
BASE_URL="http://127.0.0.1:${API_BIND_PORT:-${API_PORT:-8000}}"
mapfile -t MANAGED_UNITS < <(lark_memory_core_managed_units)
systemctl --user start "${MANAGED_UNITS[@]}"
wait_http_endpoint "${BASE_URL}/health" 30 1
wait_http_endpoint "${BASE_URL}/ready" 30 1
systemctl --user --no-pager --full status "${MANAGED_UNITS[@]}" | sed -n '1,160p'
