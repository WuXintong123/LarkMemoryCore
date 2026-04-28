# ===- test_api_contracts.py --------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Contract tests for API-user-visible behavior: request IDs, error envelopes,
# model capability metadata, landing payload, reload, and Prometheus metrics.
#
# ===---------------------------------------------------------------------------

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.auth import ApiKeyAuthManager
import api_server.main as main_module
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
async def test_root_landing_payload_exposes_discovery_links():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "lark-memory-core"
    assert body["docs"]["openapi"].endswith("/openapi.json")
    assert "/v1/models" in body["endpoints"]["public"]
    assert "/v1/admin/reload-models" in body["endpoints"]["admin"]


@pytest.mark.asyncio
async def test_request_id_header_is_returned_for_success_and_error():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/health")
        error_response = await client.post(
            "/v1/chat/completions",
            json={"model": "missing-model", "messages": []},
        )

    assert "X-Request-Id" in health_response.headers
    assert "X-Request-Id" in error_response.headers
    assert (
        error_response.json()["error"]["request_id"]
        == error_response.headers["X-Request-Id"]
    )


@pytest.mark.asyncio
async def test_model_detail_exposes_lark_memory_core_capabilities():
    fake_model = {
        "id": "model-a",
        "object": "model",
        "created": 1,
        "owned_by": "lark_memory_core",
        "_ready": True,
        "_serving_policy": {
            "api_mode": "chat",
            "prompt_style": "chatml",
            "default_max_tokens": 64,
            "max_max_tokens": 256,
            "max_input_chars": 4096,
            "request_timeout_ms": 120000,
            "stream_idle_timeout_s": 30,
            "allow_anonymous_models": False,
        },
    }
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._get_model_record",
        return_value=fake_model,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/models/model-a")

    assert response.status_code == 200
    body = response.json()
    assert body["lark_memory_core"]["api_mode"] == "chat"
    assert body["lark_memory_core"]["ready"] is True
    assert body["lark_memory_core"]["supported_endpoints"] == ["/v1/chat/completions"]
    assert "frequency_penalty" in body["lark_memory_core"]["unsupported_parameters"]


@pytest.mark.asyncio
async def test_reload_models_invalidates_cache_and_returns_public_models():
    original_cache = dict(main_module._model_cache)
    main_module._model_cache["models"] = [
        {"id": "stale-model", "object": "model", "created": 1, "owned_by": "lark_memory_core"}
    ]
    main_module._model_cache["expires_at"] = 9999999999.0
    fresh_model = SimpleNamespace(
        model_id="fresh-model",
        ready=True,
        owned_by="fresh-owner",
        created=5,
        serving_policy=None,
    )

    try:
        with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
            "api_server.main.compute_client.reload_models",
            return_value={
                "success": True,
                "model_count": 1,
                "mode": "single-node",
                "source_path": "/tmp/models.json",
                "message": "Model configuration reloaded successfully",
            },
        ), patch(
            "api_server.main.compute_client.list_models",
            return_value=[fresh_model],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/v1/admin/reload-models")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["models"][0]["id"] == "fresh-model"
        assert main_module._model_cache["models"][0]["id"] == "fresh-model"
    finally:
        main_module._model_cache.update(original_cache)


@pytest.mark.asyncio
async def test_backends_endpoint_returns_single_backend_snapshot():
    fake_backend = {
        "id": "default",
        "node_id": "default",
        "grpc_target": "127.0.0.1:9000",
        "healthy": True,
        "routes": [
            {
                "model_id": "model-a",
                "backend_model_id": "model-a",
                "weight": 100,
                "ready": True,
            }
        ],
    }

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._backend_snapshot",
        return_value=fake_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/admin/backends")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "single-node"
    assert body["backend_count"] == 1
    assert body["data"][0]["id"] == "default"


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text():
    fake_metrics = SimpleNamespace(
        total_requests=10,
        successful_requests=9,
        failed_requests=1,
        total_tokens_processed=2048,
        average_latency_ms=12.5,
        average_tokens_per_second=88.8,
        model_metrics={"model-a": {"request_count": 4, "total_tokens": 128, "average_latency_ms": 8.2}},
        rejected_requests=2,
        queued_requests=1,
        active_compute_slots=3,
        max_compute_slots=6,
        overload_rejections=1,
        watchdog_timeouts=0,
        partial_timeout_returns=0,
        request_cancellations=0,
    )
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main.compute_client.get_metrics",
        return_value=fake_metrics,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "lark_memory_core_total_requests 10" in response.text
    assert 'lark_memory_core_model_request_count{model="model-a"} 4' in response.text
