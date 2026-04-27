# ===- test_readiness.py ------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Endpoint tests for liveness/readiness behavior.
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

from api_server.main import app


@pytest.mark.asyncio
async def test_ready_returns_200_when_compute_and_models_are_available():
    fake_health = SimpleNamespace(
        healthy=True,
        version="1",
        uptime_seconds=1,
        active_requests=0,
        status_message="ok",
    )
    fake_metrics = SimpleNamespace(queued_requests=0)
    fake_models = [
        {
            "id": "model-a",
            "object": "model",
            "created": 1,
            "owned_by": "ruyi",
            "_serving_policy": {},
        }
    ]
    with patch("api_server.main.compute_client.health_check", return_value=fake_health), patch(
        "api_server.main.compute_client.get_metrics", return_value=fake_metrics
    ), patch("api_server.main._load_models_from_compute", return_value=fake_models):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_ready_returns_503_when_compute_unhealthy():
    fake_health = SimpleNamespace(
        healthy=False,
        version="1",
        uptime_seconds=1,
        active_requests=0,
        status_message="down",
    )
    fake_metrics = SimpleNamespace(queued_requests=0)
    fake_models = [
        {
            "id": "model-a",
            "object": "model",
            "created": 1,
            "owned_by": "ruyi",
            "_serving_policy": {},
        }
    ]
    with patch("api_server.main.compute_client.health_check", return_value=fake_health), patch(
        "api_server.main.compute_client.get_metrics", return_value=fake_metrics
    ), patch("api_server.main._load_models_from_compute", return_value=fake_models):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")

    assert response.status_code == 503
    assert "compute_unhealthy" in response.json()["reasons"]


def test_parse_cors_allow_origins_helper():
    from api_server.main import _parse_cors_allow_origins

    assert _parse_cors_allow_origins("") == []
    assert _parse_cors_allow_origins("https://a.example, https://b.example") == [
        "https://a.example",
        "https://b.example",
    ]
