#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export SYSTEMD_PAGER=
export RUYI_PYTHON_BIN="${RUYI_PYTHON_BIN:-$(bash "${REPO_ROOT}/ops/python_cmd.sh")}"

ruyi_python() {
  "${RUYI_PYTHON_BIN}" "$@"
}

ruyi_default_cmake_preset() {
  case "$(uname -s)" in
    Linux)
      if [[ "${CI:-}" == "true" ]]; then
        printf '%s\n' "ci-linux"
      else
        printf '%s\n' "linux-debug"
      fi
      ;;
    *)
      echo "unsupported host platform: $(uname -s)" >&2
      exit 1
      ;;
  esac
}

ruyi_cmake_preset() {
  if [[ -n "${RUYI_CMAKE_PRESET:-}" ]]; then
    printf '%s\n' "${RUYI_CMAKE_PRESET}"
  else
    ruyi_default_cmake_preset
  fi
}

ruyi_build_preset() {
  printf '%s-build\n' "$(ruyi_cmake_preset)"
}

ruyi_test_preset() {
  printf '%s-test\n' "$(ruyi_cmake_preset)"
}

# The shell entrypoints still use direct CMake commands, but they centralize preset
# selection here so docs, CI, and deploy scripts stay aligned on the same build graph.
ruyi_configure_cmake() {
  cmake --preset "$(ruyi_cmake_preset)"
}

ruyi_build_targets() {
  local build_preset
  build_preset="$(ruyi_build_preset)"
  if [[ $# -eq 0 ]]; then
    cmake --build --preset "${build_preset}"
  else
    cmake --build --preset "${build_preset}" --target "$@"
  fi
}

ruyi_ctest() {
  ctest --preset "$(ruyi_test_preset)" "$@"
}

require_systemd_user() {
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "systemd --user 不可用，请先登录该用户会话后重试。" >&2
    exit 1
  fi
}

require_systemd_user_linger() {
  local linger

  linger=$(loginctl show-user "$USER" -p Linger 2>/dev/null | awk -F= '{print $2}')
  if [[ "${linger}" == "yes" ]]; then
    return 0
  fi

  echo "部署验证要求 persistent systemd --user，但当前 loginctl lingering 未开启。" >&2
  echo "当前值: Linger=${linger:-unknown}" >&2
  echo "请让管理员执行: sudo loginctl enable-linger $USER" >&2
  echo "在 Linger=no 的情况下，SSH 会话结束后用户服务会跟随退出，因此不能把部署视为已验证。" >&2
  exit 1
}

require_repo_env() {
  if [[ ! -f "${REPO_ROOT}/.env" ]]; then
    echo "缺少 ${REPO_ROOT}/.env" >&2
    exit 1
  fi
}

wait_http_endpoint() {
  local url="$1"
  local attempts="${2:-30}"
  local delay_s="${3:-1}"

  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS --max-time 5 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${delay_s}"
  done

  echo "等待接口超时: ${url}" >&2
  return 1
}

ruyi_layout_cmd() {
  ruyi_python "${REPO_ROOT}/ops/systemd_layout.py" "$@"
}

ruyi_managed_units() {
  ruyi_layout_cmd units
}

ruyi_compute_units() {
  ruyi_layout_cmd compute-units
}

ruyi_local_compute_ports() {
  ruyi_layout_cmd ports
}

ruyi_log_paths() {
  ruyi_layout_cmd log-paths
}

validate_active_runtime_config() {
  REPO_ROOT="${REPO_ROOT}" "${RUYI_PYTHON_BIN}" - <<'PY'
import os
import sys
from pathlib import Path

repo_root = Path(os.environ["REPO_ROOT"])
sys.path.insert(0, str(repo_root / "ops"))

from runtime_paths import (
    find_model_problems,
    load_env_file,
    resolve_model_config_path,
)

env_values = load_env_file(repo_root / ".env")
active_config_path = resolve_model_config_path(repo_root, env_values)

if not active_config_path.exists():
    raise SystemExit(f"active runtime config is missing: {active_config_path}")

problems = find_model_problems(active_config_path)
if problems:
    print("[error] runtime config is not ready:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    raise SystemExit(1)

print(f"[ok] runtime config ready: {active_config_path}")
PY
}
