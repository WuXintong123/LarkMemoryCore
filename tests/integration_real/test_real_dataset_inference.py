# ===- test_real_dataset_inference.py -----------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Real integration tests against a running API server + real model + real dataset.
# No mock data or fake backend is used in this test module.
#
# Required environment variables:
#   REAL_DATASET_PATH
#   REAL_INTEGRATION_MODEL
#
# Optional:
#   REAL_INTEGRATION_BASE_URL (default: http://127.0.0.1:8000)
#   REAL_INTEGRATION_API_KEY
#   REAL_INTEGRATION_MAX_SAMPLES (default: 5)
#   REAL_INTEGRATION_TIMEOUT_S (default: 120)
#
# Dataset format: JSONL, one object per line.
# Mandatory fields:
#   - prompt: non-empty string
# Optional fields:
#   - max_tokens: integer
#   - expected_substrings: list[string]
#
# ===---------------------------------------------------------------------------

import json
import os
from typing import Any, Dict, List

import pytest
import requests


BASE_URL = os.getenv("REAL_INTEGRATION_BASE_URL", "http://127.0.0.1:8000")
MODEL_ID = os.getenv("REAL_INTEGRATION_MODEL", "").strip()
DATASET_PATH = os.getenv("REAL_DATASET_PATH", "").strip()
API_KEY = os.getenv("REAL_INTEGRATION_API_KEY", "").strip()
MAX_SAMPLES = int(os.getenv("REAL_INTEGRATION_MAX_SAMPLES", "5"))
TIMEOUT_S = float(os.getenv("REAL_INTEGRATION_TIMEOUT_S", "120"))
COMPLETION_MAX_TOKENS = int(os.getenv("REAL_INTEGRATION_COMPLETION_MAX_TOKENS", "64"))


def _require_real_env() -> None:
    if not DATASET_PATH:
        pytest.skip("REAL_DATASET_PATH is not set")
    if not MODEL_ID:
        pytest.skip("REAL_INTEGRATION_MODEL is not set")
    if not os.path.exists(DATASET_PATH):
        pytest.fail(f"REAL_DATASET_PATH does not exist: {DATASET_PATH}")


def _load_dataset(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"Invalid JSONL at line {line_no}: {exc}"
                ) from exc

            if not isinstance(row, dict):
                raise AssertionError(f"Dataset row at line {line_no} must be an object")
            prompt = row.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise AssertionError(
                    f"Dataset row at line {line_no} must include non-empty 'prompt'"
                )
            rows.append(row)

    if not rows:
        raise AssertionError("Dataset file is empty")
    return rows


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    return headers


@pytest.mark.real_integration
def test_real_dataset_chat_completions():
    _require_real_env()
    dataset = _load_dataset(DATASET_PATH)
    sample_rows = dataset[: max(1, MAX_SAMPLES)]

    for idx, row in enumerate(sample_rows):
        payload = {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": row["prompt"]}],
            "max_tokens": int(row.get("max_tokens", 128)),
            "temperature": 0.0,
        }
        response = requests.post(
            f"{BASE_URL}/v1/chat/completions",
            headers=_headers(),
            json=payload,
            timeout=TIMEOUT_S,
        )
        assert response.status_code == 200, (
            f"chat completion failed at sample {idx}, "
            f"status={response.status_code}, body={response.text}"
        )

        body = response.json()
        assistant_text = body["choices"][0]["message"]["content"]
        assert isinstance(assistant_text, str) and assistant_text.strip(), (
            f"empty model output at sample {idx}"
        )

        expected_substrings = row.get("expected_substrings", [])
        if isinstance(expected_substrings, list) and expected_substrings:
            for term in expected_substrings:
                assert isinstance(term, str) and term, (
                    f"expected_substrings must contain non-empty strings, sample {idx}"
                )
                assert term in assistant_text, (
                    f"missing expected substring '{term}' at sample {idx}"
                )


@pytest.mark.real_integration
def test_real_dataset_completions_prompt_list():
    _require_real_env()
    dataset = _load_dataset(DATASET_PATH)
    prompts = [row["prompt"] for row in dataset[: max(2, min(MAX_SAMPLES, len(dataset)))]]

    payload = {
        "model": MODEL_ID,
        "prompt": prompts,
        "max_tokens": COMPLETION_MAX_TOKENS,
        "temperature": 0.0,
    }
    response = requests.post(
        f"{BASE_URL}/v1/completions",
        headers=_headers(),
        json=payload,
        timeout=TIMEOUT_S,
    )
    assert response.status_code == 200, (
        f"completions prompt-list request failed, "
        f"status={response.status_code}, body={response.text}"
    )
    body = response.json()
    choices = body.get("choices", [])
    assert len(choices) == len(prompts), (
        f"expected {len(prompts)} choices, got {len(choices)}"
    )
    for idx, choice in enumerate(choices):
        text = choice.get("text", "")
        assert isinstance(text, str) and text.strip(), (
            f"empty completion output at choice {idx}"
        )
