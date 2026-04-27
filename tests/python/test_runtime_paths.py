# ===- test_runtime_paths.py --------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Unit tests for shared runtime config helpers used by deployment scripts.
#
# ===---------------------------------------------------------------------------

import os
import sys
from pathlib import Path

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from ops.runtime_paths import (
    find_model_problems,
    is_placeholder_cli_path,
    load_env_file,
)


def test_placeholder_cli_path_detection():
    assert is_placeholder_cli_path("")
    assert is_placeholder_cli_path("/path/to/model-cli")
    assert is_placeholder_cli_path("/opt/PLACEHOLDER/model-cli")
    assert not is_placeholder_cli_path("/usr/local/bin/buddy-cli")


def test_load_env_file_strips_quotes(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text('MODELS_CONFIG_FILE="custom-models.json"\n', encoding="utf-8")

    values = load_env_file(env_file)

    assert values["MODELS_CONFIG_FILE"] == "custom-models.json"


def test_find_model_problems_reports_placeholder_and_missing_binary(tmp_path: Path):
    model_config = tmp_path / "models.json"
    model_config.write_text(
        """
        {
          "models": [
            {
              "id": "placeholder-model",
              "tool": {"cli_path": "/path/to/placeholder-cli"}
            },
            {
              "id": "missing-model",
              "tool": {"cli_path": "/tmp/definitely-missing-cli"}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    problems = find_model_problems(model_config)

    assert any("placeholder-model" in problem for problem in problems)
    assert any("missing-model" in problem for problem in problems)
