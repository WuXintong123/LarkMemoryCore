"""Cross-cutting FastAPI dependency guards."""

from __future__ import annotations

from importlib import import_module
from typing import List, Optional

from fastapi import Depends, Header, HTTPException, Request

from .auth import ApiKeyPrincipal, verify_api_key


def _main_module():
    return import_module("api_server.main")


async def check_rate_limit(
    request: Request,
    authorization: Optional[str] = Header(None),
    principal: Optional[ApiKeyPrincipal] = Depends(verify_api_key),
):
    main_module = _main_module()
    key_parts: List[str] = []
    parsed_api_key = main_module.auth_manager.rate_limit_subject(principal, authorization)
    client_ip = request.client.host if request.client else "unknown"
    model_id = "-"

    if "model" in main_module.rate_limit_scopes and request.method.upper() == "POST":
        if request.url.path in ("/v1/chat/completions", "/v1/completions"):
            try:
                payload = await request.json()
                model_value = payload.get("model")
                if isinstance(model_value, str) and model_value:
                    model_id = model_value
            except Exception:
                model_id = "-"

    for scope in main_module.rate_limit_scopes:
        if scope == "api_key":
            key_parts.append(f"api_key={parsed_api_key}")
        elif scope == "ip":
            key_parts.append(f"ip={client_ip}")
        elif scope == "model":
            key_parts.append(f"model={model_id}")
        elif scope == "path":
            key_parts.append(f"path={request.url.path}")

    client_key = "|".join(key_parts) if key_parts else f"ip={client_ip}"
    if not await main_module.rate_limiter.check(client_key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later.",
        )
