#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${LARK_MEMORY_CORE_PYTHON_BIN:-}" ]]; then
  printf '%s\n' "${LARK_MEMORY_CORE_PYTHON_BIN}"
  exit 0
fi

for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if ! command -v "${candidate}" >/dev/null 2>&1; then
    continue
  fi

  if "${candidate}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    command -v "${candidate}"
    exit 0
  fi
done

echo "[error] Python 3.10+ interpreter not found. Set LARK_MEMORY_CORE_PYTHON_BIN to a supported interpreter." >&2
exit 1
