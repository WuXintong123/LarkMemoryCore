import os
import subprocess
import sys
from pathlib import Path

import pytest


CLI_PATH = Path("competition/feishu_office/runtime/feishu_office_hf_cli.py")


@pytest.mark.skipif(
    "FEISHU_OFFICE_DAEMON_PORT" not in os.environ or "FEISHU_OFFICE_TRAIN_PYTHON" not in os.environ,
    reason="Real daemon runtime is not configured",
)
def test_real_daemon_ping_contract():
    completed = subprocess.run(
        [
            os.environ["FEISHU_OFFICE_TRAIN_PYTHON"],
            str(CLI_PATH),
            "--daemon-port",
            os.environ["FEISHU_OFFICE_DAEMON_PORT"],
            "--ping",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(
    "FEISHU_OFFICE_DAEMON_PORT" not in os.environ or "FEISHU_OFFICE_TRAIN_PYTHON" not in os.environ,
    reason="Real daemon runtime is not configured",
)
def test_real_daemon_generates_text_for_office_prompt():
    completed = subprocess.run(
        [
            os.environ["FEISHU_OFFICE_TRAIN_PYTHON"],
            str(CLI_PATH),
            "--daemon-port",
            os.environ["FEISHU_OFFICE_DAEMON_PORT"],
            "--max-tokens",
            "48",
        ],
        input="User: 请根据材料输出正式周报格式，包含本周进展、风险与关注、下周计划。\n\nAssistant:",
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip()

