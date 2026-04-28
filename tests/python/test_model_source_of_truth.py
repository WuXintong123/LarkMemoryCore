# ===- test_model_source_of_truth.py ------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Tests that model listing/validation uses Compute Server as the single source
# of truth.
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
import api_server.main as main_module
from api_server.main import app


def _assert_lark_memory_core_capabilities(payload: dict) -> None:
    lark_memory_core = payload["lark_memory_core"]
    assert "supported_endpoints" in lark_memory_core
    assert "supported_parameters" in lark_memory_core
    assert "unsupported_parameters" in lark_memory_core
    assert "ready" in lark_memory_core


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
async def test_v1_models_comes_from_compute_server():
    fake_models = [
        {
            "id": "compute/model-a",
            "object": "model",
            "created": 100,
            "owned_by": "compute-owner",
        }
    ]
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._load_models_from_compute", return_value=fake_models
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/models")
            assert response.status_code == 200
            body = response.json()
            assert body["object"] == "list"
            assert body["data"][0]["id"] == fake_models[0]["id"]
            assert body["data"][0]["owned_by"] == fake_models[0]["owned_by"]
            _assert_lark_memory_core_capabilities(body["data"][0])


@pytest.mark.asyncio
async def test_admin_model_register_returns_not_supported():
    with patch("api_server.main.auth_manager", _disabled_auth_manager()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/admin/models/register",
                json={"id": "new-model"},
            )
            assert response.status_code == 501
            body = response.json()
            assert body["error"]["type"] == "not_supported_error"


@pytest.mark.asyncio
async def test_admin_model_unregister_returns_not_supported():
    with patch("api_server.main.auth_manager", _disabled_auth_manager()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete("/v1/admin/models/test-model")
            assert response.status_code == 501
            body = response.json()
            assert body["error"]["type"] == "not_supported_error"


@pytest.mark.asyncio
async def test_model_metadata_is_preserved_from_compute_server():
    original_cache = dict(main_module._model_cache)
    main_module._model_cache["models"] = []
    main_module._model_cache["expires_at"] = 0.0
    fake_model = SimpleNamespace(
        model_id="compute/model-a",
        ready=True,
        owned_by="compute-owner",
        created=1737363858,
    )

    try:
        with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
            "api_server.main.compute_client.list_models",
            return_value=[fake_model],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/v1/models")

        assert response.status_code == 200
        model = response.json()["data"][0]
        assert model["id"] == "compute/model-a"
        assert model["created"] == 1737363858
        assert model["owned_by"] == "compute-owner"
        assert model["lark_memory_core"]["ready"] is True
    finally:
        main_module._model_cache.update(original_cache)
