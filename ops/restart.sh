#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/common.sh"
require_systemd_user
"${SCRIPT_DIR}/preflight.sh"
mapfile -t MANAGED_UNITS < <(ruyi_managed_units)
systemctl --user restart "${MANAGED_UNITS[@]}"
"${SCRIPT_DIR}/smoke_prod.sh"
systemctl --user --no-pager --full status "${MANAGED_UNITS[@]}" | sed -n '1,160p'
