#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${SCRIPT_DIR}/feishu_office_competition_common.sh"

stop_pid_file_if_running "${API_PID_FILE}"
stop_pid_file_if_running "${COMPUTE_PID_FILE}"
stop_pid_file_if_running "${DAEMON_PID_FILE}"
stop_port_if_listening "${API_PORT}"
stop_port_if_listening "${COMPUTE_PORT}"
stop_port_if_listening "${DAEMON_PORT}"

echo "feishu-office competition runtime stopped"
