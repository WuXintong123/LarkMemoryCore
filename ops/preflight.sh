#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "${SCRIPT_DIR}/common.sh"

require_systemd_user
require_repo_env

cd "${REPO_ROOT}"
validate_active_runtime_config

if [[ ! -x "${REPO_ROOT}/build/bin/compute_server" ]]; then
  echo "缺少可执行文件: ${REPO_ROOT}/build/bin/compute_server" >&2
  exit 1
fi

if [[ ! -f "${REPO_ROOT}/api_server/proto/compute_pb2.py" || ! -f "${REPO_ROOT}/api_server/proto/compute_pb2_grpc.py" ]]; then
  echo "缺少 Python protobuf 生成物，请先构建 generate_python_proto 目标。" >&2
  exit 1
fi

REPO_ROOT="${REPO_ROOT}" "${LARK_MEMORY_CORE_PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

repo_root = Path(os.environ["REPO_ROOT"])
env_path = repo_root / ".env"
env_values = {}
for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env_values[key.strip()] = value.strip().strip("'\"")

api_keys_file = env_values.get("API_KEYS_FILE", "")
api_keys_json = env_values.get("API_KEYS_JSON", "")
if api_keys_file:
    path = Path(api_keys_file)
    if not path.exists():
        raise SystemExit(f"API_KEYS_FILE does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
elif api_keys_json:
    payload = json.loads(api_keys_json)
else:
    payload = None

if payload is not None:
    keys = payload["keys"] if isinstance(payload, dict) and "keys" in payload else payload
    if not isinstance(keys, list) or not keys:
        raise SystemExit("API key config must contain at least one key")

print("api_key_config: ok")
PY

mapfile -t MANAGED_UNITS < <(lark_memory_core_managed_units)
units_active=0
for unit in "${MANAGED_UNITS[@]}"; do
  if systemctl --user is-active --quiet "${unit}"; then
    units_active=1
    break
  fi
done

if [[ "${units_active}" -eq 0 ]]; then
  mapfile -t COMPUTE_PORTS < <(lark_memory_core_local_compute_ports)
  for port in $(printf '%s\n' 8000 18443 "${COMPUTE_PORTS[@]}" | sed '/^$/d' | sort -u); do
    if ss -lnt "( sport = :${port} )" | grep -q ":${port}"; then
      echo "端口已被占用: ${port}" >&2
      exit 1
    fi
  done
fi

echo "[ok] preflight checks passed"
