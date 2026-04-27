# ===- test_rate_limiter.py ---------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Tests for multi-tenant token bucket key construction.
#
# ===---------------------------------------------------------------------------

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.auth import ApiKeyPrincipal
from api_server.main import check_rate_limit


class _FakeRequest:
    def __init__(self, path: str, method: str, model: str, ip: str = "1.2.3.4"):
        self.url = SimpleNamespace(path=path)
        self.method = method
        self.client = SimpleNamespace(host=ip)
        self._payload = {"model": model}

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_rate_limit_key_contains_api_key_ip_model_path():
    request = _FakeRequest("/v1/chat/completions", "POST", "model-x")
    mock_check = AsyncMock(return_value=True)
    principal = ApiKeyPrincipal(
        key_id="tenant-a",
        scopes=frozenset({"inference"}),
        allowed_models=None,
    )
    with patch("api_server.main.rate_limit_scopes", ["api_key", "ip", "model", "path"]), \
         patch("api_server.main.rate_limiter.check", mock_check):
        await check_rate_limit(
            request,
            authorization="Bearer sk-test",
            principal=principal,
        )

    passed_key = mock_check.await_args.args[0]
    assert "api_key=key_id:tenant-a" in passed_key
    assert "ip=1.2.3.4" in passed_key
    assert "model=model-x" in passed_key
    assert "path=/v1/chat/completions" in passed_key


@pytest.mark.asyncio
async def test_rate_limit_uses_anonymous_when_no_auth_header():
    request = _FakeRequest("/v1/completions", "POST", "model-y")
    mock_check = AsyncMock(return_value=True)
    with patch("api_server.main.rate_limit_scopes", ["api_key", "ip", "model"]), \
         patch("api_server.main.rate_limiter.check", mock_check):
        await check_rate_limit(request, authorization=None, principal=None)

    passed_key = mock_check.await_args.args[0]
    assert "api_key=anonymous" in passed_key
    assert "ip=1.2.3.4" in passed_key
    assert "model=model-y" in passed_key
