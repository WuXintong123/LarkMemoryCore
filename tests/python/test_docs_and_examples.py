# ===- test_docs_and_examples.py ----------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Docs/example contract tests to keep README, OpenAPI, and shipped examples
# aligned with the actual public API.
#
# ===---------------------------------------------------------------------------

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api_server.main import app


README_PATH = _project_root / "README.md"
OPENCLAW_EXAMPLE_PATH = _project_root / "examples" / "openclaw_config.jsonc"
OPENCLAW_FEISHU_RUNBOOK_PATH = _project_root / "docs" / "openclaw-feishu-runbook.md"
OPENCLAW_FEISHU_MATRIX_PATH = _project_root / "docs" / "openclaw-feishu-acceptance-matrix.md"
OPENCLAW_FEISHU_LOG_CHECKS_PATH = _project_root / "docs" / "openclaw-feishu-log-checks.md"
OPENCLAW_FEISHU_NOTE_TEMPLATE_PATH = (
    _project_root / "docs" / "openclaw-feishu-verification-note-template.md"
)
OPENCLAW_FEISHU_NOTE_PATH = (
    _project_root / "docs" / "openclaw-feishu-verification-note-2026-04-13.md"
)
PUBLIC_DOC_PATHS = (
    README_PATH,
    _project_root / "docs" / "validation-gates.md",
    OPENCLAW_FEISHU_RUNBOOK_PATH,
    OPENCLAW_FEISHU_MATRIX_PATH,
    OPENCLAW_FEISHU_LOG_CHECKS_PATH,
)


def test_readme_mentions_key_public_and_admin_routes():
    readme = README_PATH.read_text(encoding="utf-8")
    for route in (
        "GET /ready",
        "GET /v1/models/{model_id}",
        "GET /metrics",
        "POST /v1/admin/reload-models",
    ):
        assert route in readme


def test_openapi_exposes_key_paths():
    schema = app.openapi()
    for path in (
        "/",
        "/ready",
        "/v1/models/{model_id}",
        "/metrics",
        "/v1/admin/reload-models",
    ):
        assert path in schema["paths"]


def test_public_docs_use_cmake_and_ctest_entrypoints():
    readme = README_PATH.read_text(encoding="utf-8")
    assert "cmake --preset" in readme
    assert "ctest --preset" in readme

    make_pattern = re.compile(r"(^|[^A-Za-z])make([^A-Za-z]|$)")
    for path in PUBLIC_DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        assert make_pattern.search(text) is None, f"'make' leaked into {path}"

    disallowed_terms = (
        "macos-debug",
        "brew install",
        "cluster.json",
        "reload-cluster",
        "docker compose",
    )
    for path in PUBLIC_DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        for term in disallowed_terms:
            assert term not in text, f"'{term}' leaked into {path}"


def test_validation_gates_document_grpc_contract_gate():
    text = (_project_root / "docs" / "validation-gates.md").read_text(encoding="utf-8")
    assert "Automated gRPC Contract Gate" in text
    assert "tests/python/test_grpc_contracts.py" in text
    assert "hello!" in text


def test_ci_and_presets_are_linux_only():
    ci_text = (_project_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    presets_text = (_project_root / "CMakePresets.json").read_text(encoding="utf-8")
    assert "macos-latest" not in ci_text
    assert "brew --prefix" not in ci_text
    assert "macos-debug" not in presets_text


def test_python_example_help_runs():
    result = subprocess.run(
        [sys.executable, "examples/openai_sdk_compat.py", "--help"],
        cwd=_project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Prompt text to send through the OpenAI SDK." in result.stdout


def test_javascript_example_help_runs_when_node_is_available():
    node = shutil.which("node")
    if not node:
        return
    result = subprocess.run(
        [node, "examples/javascript/openai_sdk_compat.mjs", "--help"],
        cwd=_project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Usage: node openai_sdk_compat.mjs [prompt]" in result.stdout


def test_openclaw_example_documents_isolated_verify_prerequisites():
    text = OPENCLAW_EXAMPLE_PATH.read_text(encoding="utf-8")
    for required_snippet in (
        'baseUrl": "http://127.0.0.1:18100/v1"',
        'CLUSTER_CONFIG_FILE=""',
        "buddy-ascend",
        "lark_memory_stream",
        '"primary": "lark_memory_stream/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"',
        "default_max_tokens = 64",
        "request_timeout_ms = 30000",
        "stream_idle_timeout_s = 30",
        "max_input_chars",
        "/v1/chat/completions",
        "compute prompt 只取最后一条 user",
        "verify/check",
    ):
        assert required_snippet in text


def test_openclaw_feishu_runbook_documents_trace_and_release_flow():
    text = OPENCLAW_FEISHU_RUNBOOK_PATH.read_text(encoding="utf-8")
    for required_snippet in (
        "buddy-ascend",
        "LARK_MEMORY_CORE_DEBUG_PROMPT_IO=1",
        "raw request",
        "compute prompt 只保留最后一条 `user`",
        "lark_memory_stream",
        "/v1/chat/completions",
        "ops/openclaw_feishu_buddy_ascend_check.sh",
        "DM + 非流式",
        "群聊 `@bot` + 流式",
    ):
        assert required_snippet in text


def test_openclaw_feishu_docs_ship_matrix_log_checks_and_verification_note():
    matrix_text = OPENCLAW_FEISHU_MATRIX_PATH.read_text(encoding="utf-8")
    log_checks_text = OPENCLAW_FEISHU_LOG_CHECKS_PATH.read_text(encoding="utf-8")
    template_text = OPENCLAW_FEISHU_NOTE_TEMPLATE_PATH.read_text(encoding="utf-8")
    dated_note_text = OPENCLAW_FEISHU_NOTE_PATH.read_text(encoding="utf-8")

    assert "群聊 `@bot` + 流式" in matrix_text
    assert "Only text content parts are supported" in matrix_text
    assert "API server received raw request" in log_checks_text
    assert "Compute server received prompt" in log_checks_text
    assert "round1-token" in log_checks_text
    assert "Date:" in template_text
    assert "Host:" in template_text
    assert "Date: 2026-04-13" in dated_note_text
    assert "Host: buddy-ascend" in dated_note_text
    assert "DM + stream" in dated_note_text


def test_openclaw_feishu_ops_script_is_present_and_mentions_trace_assertions():
    script_text = (_project_root / "ops" / "openclaw_feishu_buddy_ascend_check.sh").read_text(
        encoding="utf-8"
    )
    for required_snippet in (
        "--scenario",
        "--trace-token",
        "API server received raw request",
        "Compute server received prompt",
        "summary.json",
        "summary.md",
    ):
        assert required_snippet in script_text
