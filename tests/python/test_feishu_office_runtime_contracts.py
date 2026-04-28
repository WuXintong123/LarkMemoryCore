import os
import subprocess
import sys
from io import BytesIO, StringIO
from pathlib import Path

import pytest


CLI_PATH = Path("competition/feishu_office/runtime/feishu_office_hf_cli.py")


def test_cli_timeout_is_configurable_for_cold_model_generation(monkeypatch):
    from competition.feishu_office.runtime import feishu_office_hf_cli

    captured = {}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def sendall(self, payload):
            captured["payload"] = payload

        def makefile(self, mode):
            captured["mode"] = mode
            return BytesIO(b'{"type":"done"}\n')

    def fake_create_connection(address, timeout):
        captured["address"] = address
        captured["timeout"] = timeout
        return FakeSocket()

    monkeypatch.setattr(
        feishu_office_hf_cli.socket,
        "create_connection",
        fake_create_connection,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "feishu_office_hf_cli.py",
            "--daemon-host",
            "127.0.0.1",
            "--daemon-port",
            "19600",
            "--timeout-s",
            "181",
        ],
    )
    monkeypatch.setattr(sys, "stdin", StringIO("User: hello\n"))

    assert feishu_office_hf_cli.main() == 0
    assert captured["address"] == ("127.0.0.1", 19600)
    assert captured["timeout"] == 181
    assert b'"type": "generate"' in captured["payload"]


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
