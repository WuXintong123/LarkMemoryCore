# ===- test_api_key_auth.py ---------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Tests for API key authentication and authorization.
#
# ===---------------------------------------------------------------------------

import hashlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.auth import ApiKeyAuthManager
from api_server.main import app, check_rate_limit


def _assert_ruyi_capabilities(payload: dict) -> None:
    ruyi = payload["ruyi"]
    assert "supported_endpoints" in ruyi
    assert "supported_parameters" in ruyi
    assert "unsupported_parameters" in ruyi


def _disabled_auth_manager() -> ApiKeyAuthManager:
    return ApiKeyAuthManager.from_config(
        legacy_api_key="",
        legacy_key_id="default",
        legacy_scopes="models:read,inference,admin",
        legacy_allowed_models="",
        api_keys_file="",
        api_keys_json="",
    )


def _manager_from_json(json_blob: str) -> ApiKeyAuthManager:
    return ApiKeyAuthManager.from_config(
        legacy_api_key="",
        legacy_key_id="default",
        legacy_scopes="models:read,inference,admin",
        legacy_allowed_models="",
        api_keys_file="",
        api_keys_json=json_blob,
    )


class _FakeRequest:
    def __init__(self, path: str, method: str, model: str, ip: str = "1.2.3.4"):
        self.url = SimpleNamespace(path=path)
        self.method = method
        self.client = SimpleNamespace(host=ip)
        self.state = SimpleNamespace(auth_key_id=None)
        self._payload = {"model": model}

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_models_require_auth_when_api_keys_enabled():
    manager = _manager_from_json(
        '{"keys":[{"key_id":"tenant-a","secret":"sk-tenant-a"}]}'
    )
    fake_models = [
        {
            "id": "model-a",
            "object": "model",
            "created": 1,
            "owned_by": "ruyi",
            "_serving_policy": {"allow_anonymous_models": False},
        }
    ]
    with patch("api_server.main.auth_manager", manager), patch(
        "api_server.main._load_models_from_compute",
        return_value=fake_models,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/models")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_inference_key_cannot_access_admin_metrics():
    manager = _manager_from_json(
        '{"keys":[{"key_id":"tenant-a","secret":"sk-tenant-a","scopes":["inference"]}]}'
    )
    with patch("api_server.main.auth_manager", manager):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/admin/metrics",
                headers={"Authorization": "Bearer sk-tenant-a"},
            )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "API key is not permitted to access this endpoint"


@pytest.mark.asyncio
async def test_admin_key_can_access_admin_metrics():
    manager = _manager_from_json(
        '{"keys":[{"key_id":"ops-admin","secret":"sk-admin","scopes":["admin"]}]}'
    )
    fake_metrics = SimpleNamespace(
        total_requests=10,
        successful_requests=9,
        failed_requests=1,
        total_tokens_processed=2048,
        average_latency_ms=12.5,
        average_tokens_per_second=88.8,
        model_metrics={"model-a": {"requests": 10}},
        rejected_requests=2,
        queued_requests=1,
        active_compute_slots=3,
        max_compute_slots=6,
    )
    with patch("api_server.main.auth_manager", manager), patch(
        "api_server.main.compute_client.get_metrics",
        return_value=fake_metrics,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/admin/metrics",
                headers={"Authorization": "Bearer sk-admin"},
            )

    assert response.status_code == 200
    assert response.json()["total_requests"] == 10
    assert response.json()["rejected_requests"] == 2
    assert response.json()["queued_requests"] == 1
    assert response.json()["active_compute_slots"] == 3
    assert response.json()["max_compute_slots"] == 6


@pytest.mark.asyncio
async def test_model_allowlist_filters_model_list():
    manager = _manager_from_json(
        '{"keys":[{"key_id":"tenant-a","secret":"sk-tenant-a","scopes":["inference"],"models":["model-a"]}]}'
    )
    fake_models = [
        {"id": "model-a", "object": "model", "created": 1, "owned_by": "ruyi"},
        {"id": "model-b", "object": "model", "created": 2, "owned_by": "ruyi"},
    ]
    with patch("api_server.main.auth_manager", manager), patch(
        "api_server.main._load_models_from_compute",
        return_value=fake_models,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/models",
                headers={"Authorization": "Bearer sk-tenant-a"},
            )

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == fake_models[0]["id"]
    _assert_ruyi_capabilities(response.json()["data"][0])


@pytest.mark.asyncio
async def test_model_allowlist_blocks_chat_completion():
    manager = _manager_from_json(
        '{"keys":[{"key_id":"tenant-a","secret":"sk-tenant-a","scopes":["inference"],"models":["model-a"]}]}'
    )
    payload = {
        "model": "model-b",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("api_server.main.auth_manager", manager):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer sk-tenant-a"},
                json=payload,
            )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "API key is not permitted to access this model"


@pytest.mark.asyncio
async def test_secret_sha256_configuration_authenticates():
    secret = "sk-hashed"
    secret_sha256 = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    manager = _manager_from_json(
        (
            '{"keys":[{"key_id":"hashed-client","secret_sha256":"%s",'
            '"scopes":["models:read"]}]}'
        )
        % secret_sha256
    )
    fake_models = [
        {"id": "model-a", "object": "model", "created": 1, "owned_by": "ruyi"}
    ]
    with patch("api_server.main.auth_manager", manager), patch(
        "api_server.main._load_models_from_compute",
        return_value=fake_models,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/models",
                headers={"Authorization": f"Bearer {secret}"},
            )

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == fake_models[0]["id"]
    _assert_ruyi_capabilities(response.json()["data"][0])


@pytest.mark.asyncio
async def test_rate_limit_uses_key_id_instead_of_raw_secret():
    manager = _manager_from_json(
        '{"keys":[{"key_id":"tenant-a","secret":"sk-tenant-a","scopes":["inference"]}]}'
    )
    request = _FakeRequest("/v1/chat/completions", "POST", "model-a")
    principal = manager.authenticate("Bearer sk-tenant-a")
    mock_check = AsyncMock(return_value=True)

    with patch("api_server.main.auth_manager", manager), patch(
        "api_server.main.rate_limit_scopes",
        ["api_key", "ip", "model", "path"],
    ), patch("api_server.main.rate_limiter.check", mock_check):
        await check_rate_limit(
            request,
            authorization="Bearer sk-tenant-a",
            principal=principal,
        )

    passed_key = mock_check.await_args.args[0]
    assert "api_key=key_id:tenant-a" in passed_key
    assert "sk-tenant-a" not in passed_key
    assert "ip=1.2.3.4" in passed_key
    assert "model=model-a" in passed_key
    assert "path=/v1/chat/completions" in passed_key


@pytest.mark.asyncio
async def test_no_auth_manager_still_allows_public_model_list():
    fake_models = [
        {"id": "model-a", "object": "model", "created": 1, "owned_by": "ruyi"}
    ]
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._load_models_from_compute",
        return_value=fake_models,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == fake_models[0]["id"]
    _assert_ruyi_capabilities(response.json()["data"][0])
