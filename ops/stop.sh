#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/common.sh"
require_systemd_user
mapfile -t MANAGED_UNITS < <(ruyi_managed_units)
systemctl --user stop "${MANAGED_UNITS[@]}"
systemctl --user --no-pager --full status "${MANAGED_UNITS[@]}" | sed -n '1,160p'
