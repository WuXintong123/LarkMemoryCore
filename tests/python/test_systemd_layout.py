# ===- test_systemd_layout.py -------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Tests for local systemd unit layout resolution in single-node deployments.
#
# ===---------------------------------------------------------------------------

import os
import sys
from pathlib import Path

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from ops.systemd_layout import (
    build_layout,
    command_log_paths,
    command_write_target,
)


def test_build_layout_single_node(tmp_path: Path):
    layout = build_layout(
        tmp_path,
        {
            "COMPUTE_SERVER_ADDRESS": "127.0.0.1:9000",
            "MODELS_CONFIG_FILE": "models.json",
        },
    )

    assert layout["managed_units"] == [
        "ruyi-compute.service",
        "ruyi-api.service",
        "ruyi-proxy.service",
    ]
    assert layout["managed_log_paths"] == [
        "%h/ruyi-serving/.run/systemd-compute.log",
        "%h/ruyi-serving/.run/systemd-api.log",
        "%h/ruyi-serving/.run/systemd-proxy.log",
    ]
    assert layout["local_compute_ports"] == [9000]


def test_write_target_includes_all_managed_units(tmp_path: Path):
    layout = {
        "managed_units": [
            "ruyi-compute.service",
            "ruyi-api.service",
            "ruyi-proxy.service",
        ]
    }
    target_path = tmp_path / "ruyi-serving.target"

    command_write_target(layout, target_path)

    content = target_path.read_text(encoding="utf-8")
    assert "ruyi-compute.service" in content
    assert "ruyi-api.service" in content
    assert "ruyi-proxy.service" in content


def test_command_log_paths_prints_managed_logs(capsys):
    layout = {
        "managed_log_paths": [
            "%h/ruyi-serving/.run/systemd-compute.log",
            "%h/ruyi-serving/.run/systemd-api.log",
        ]
    }

    command_log_paths(layout)

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "%h/ruyi-serving/.run/systemd-compute.log",
        "%h/ruyi-serving/.run/systemd-api.log",
    ]
