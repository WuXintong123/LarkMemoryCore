#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/common.sh"
require_systemd_user
mapfile -t MANAGED_UNITS < <(ruyi_managed_units)
ARGS=()
for unit in "${MANAGED_UNITS[@]}"; do
  ARGS+=(-u "${unit}")
done
journalctl --user "${ARGS[@]}" -f
