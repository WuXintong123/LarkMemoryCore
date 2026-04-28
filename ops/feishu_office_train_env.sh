#!/usr/bin/env bash
set -euo pipefail

VENV_PATH="${FEISHU_OFFICE_TRAIN_VENV:-${HOME}/.venvs/lark-memory-feishu-office}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -d "${VENV_PATH}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_PATH}"
fi

source "${VENV_PATH}/bin/activate"
python -m pip install --upgrade pip wheel
python -m pip install --index-url https://download.pytorch.org/whl/cu121 torch==2.5.1 torchvision==0.20.1
python -m pip install -r competition/feishu_office/requirements-train.txt
python -m pip install -r requirements-dev.txt

python - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device", torch.cuda.get_device_name(0))
PY

