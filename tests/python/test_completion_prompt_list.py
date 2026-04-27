# ===- test_completion_prompt_list.py -----------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Endpoint tests for prompt list handling in /v1/completions.
#
# ===---------------------------------------------------------------------------

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.auth import ApiKeyAuthManager
from api_server.main import app


def _disabled_auth_manager() -> ApiKeyAuthManager:
    return ApiKeyAuthManager.from_config(
        legacy_api_key="",
        legacy_key_id="default",
        legacy_scopes="models:read,inference,admin",
        legacy_allowed_models="",
        api_keys_file="",
        api_keys_json="",
    )


@pytest.mark.asyncio
async def test_non_stream_completion_supports_prompt_list():
    async def _ensure_model_available(_model_id):
        return None

    call_count = {"n": 0}

    def _process_with_stats(prompt, *args, **kwargs):
        call_count["n"] += 1
        return SimpleNamespace(
            output=f"out:{prompt}",
            request_id=f"req-{call_count['n']}",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
        )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), \
         patch("api_server.main._ensure_model_available", side_effect=_ensure_model_available), \
         patch("api_server.main._get_model_record", return_value=None), \
         patch("api_server.main.compute_client.process_with_stats", side_effect=_process_with_stats):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/completions",
                json={
                    "model": "test-model",
                    "prompt": ["p1", "p2", "p3"],
                    "stream": False,
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert len(body["choices"]) == 3
    assert body["choices"][0]["text"] == "out:p1"
    assert body["choices"][1]["text"] == "out:p2"
    assert body["choices"][2]["text"] == "out:p3"


@pytest.mark.asyncio
async def test_stream_completion_rejects_multiple_prompts():
    async def _ensure_model_available(_model_id):
        return None

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), \
         patch("api_server.main._ensure_model_available", side_effect=_ensure_model_available), \
         patch("api_server.main._get_model_record", return_value=None):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/completions",
                json={
                    "model": "test-model",
                    "prompt": ["p1", "p2"],
                    "stream": True,
                },
            )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "unsupported_parameter"
