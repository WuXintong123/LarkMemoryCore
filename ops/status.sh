#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/common.sh"
require_systemd_user
ADMIN_KEY_FILE="$HOME/.config/ruyi-serving/credentials/admin_api_key.txt"
METRICS_ARGS=()
if [[ -f "${ADMIN_KEY_FILE}" ]]; then
  METRICS_ARGS=(-H "Authorization: Bearer $(cat "${ADMIN_KEY_FILE}")")
fi
mapfile -t MANAGED_UNITS < <(ruyi_managed_units)
systemctl --user --no-pager --full status "${MANAGED_UNITS[@]}" | sed -n '1,220p'
wait_http_endpoint "http://127.0.0.1:8000/health" 30 1
echo '--- root ---'
curl -fsS --max-time 5 http://127.0.0.1:8000/ && echo
echo '--- health ---'
curl -fsS --max-time 5 http://127.0.0.1:8000/health && echo
echo '--- ready ---'
curl -fsS --max-time 10 http://127.0.0.1:8000/ready && echo
echo '--- detailed ---'
curl -fsS --max-time 10 http://127.0.0.1:8000/health/detailed && echo
echo '--- metrics ---'
if [[ ${#METRICS_ARGS[@]} -gt 0 ]]; then
  curl -fsS --max-time 10 "${METRICS_ARGS[@]}" http://127.0.0.1:8000/metrics && echo
else
  echo 'admin API key not found, skipping /metrics scrape'
fi
echo '--- backends ---'
if [[ ${#METRICS_ARGS[@]} -gt 0 ]]; then
  curl -fsS --max-time 10 "${METRICS_ARGS[@]}" http://127.0.0.1:8000/v1/admin/backends && echo
else
  echo 'admin API key not found, skipping /v1/admin/backends'
fi
echo '--- proxy https ---'
curl -kfsS --max-time 10 https://127.0.0.1:18443/health && echo
