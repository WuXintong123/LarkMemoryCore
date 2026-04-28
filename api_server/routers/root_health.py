"""Root and health-related routes."""

from __future__ import annotations

from importlib import import_module
from typing import List

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter()


def _main_module():
    return import_module("api_server.main")


@router.get("/")
async def root(request: Request):
    main_module = _main_module()
    base_url = main_module._request_base_url(request)
    auth_hint = (
        "Provide Authorization: Bearer <API key> for protected endpoints."
        if main_module.auth_manager.enabled
        else "Authentication is currently disabled for this API server."
    )
    return {
        "status": "ok",
        "service": "lark-memory-core",
        "version": main_module.APP_VERSION,
        "docs": {
            "openapi": f"{base_url}{main_module.OPENAPI_SCHEMA_PATH}",
            "swagger_ui": f"{base_url}{main_module.OPENAI_DOC_PATH}",
        },
        "endpoints": {
            "public": list(main_module.PUBLIC_ENDPOINTS),
            "admin": list(main_module.ADMIN_ENDPOINTS),
        },
        "compatibility": {
            "openai_style_endpoints": [
                "/v1/chat/completions",
                "/v1/completions",
                "/v1/models",
                "/v1/models/{model_id}",
            ],
            "supported_parameters": list(main_module.SUPPORTED_INFERENCE_PARAMETERS),
            "unsupported_parameters": list(main_module.UNSUPPORTED_INFERENCE_PARAMETERS),
        },
        "auth": {
            "enabled": main_module.auth_manager.enabled,
            "hint": auth_hint,
        },
        "examples": {
            "list_models": f"curl {base_url}/v1/models",
            "health": f"curl {base_url}/ready",
            "chat_completion": (
                f"curl {base_url}/v1/chat/completions "
                '-H "Content-Type: application/json" '
                "-d '{\"model\":\"your-model-id\",\"messages\":[{\"role\":\"user\","
                "\"content\":\"Say READY only.\"}],\"max_tokens\":16}'"
            ),
        },
    }


@router.get("/health")
async def health():
    return {"status": "healthy"}


@router.get("/ready")
async def ready(request: Request):
    main_module = _main_module()
    request_id = main_module._ensure_request_id(request)
    try:
        models = await main_module._load_models_from_compute(force_refresh=True)
        backend = await main_module._backend_snapshot(force_refresh=False)
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reasons": ["compute_unreachable"],
                **main_module._make_error_detail(
                    str(exc),
                    error_type="service_unavailable_error",
                    request_id=request_id,
                    code="compute_unreachable",
                ),
            },
        )

    reasons: List[str] = []
    ready_models = [
        model["id"]
        for model in models
        if bool(model.get("_ready", True))
    ]
    if not models:
        reasons.append("no_models")
    elif not ready_models:
        reasons.append("no_ready_models")
    if not backend.get("healthy", False):
        reasons.append("compute_unhealthy")
    if main_module.MAX_QUEUED_REQUESTS > 0:
        metrics = backend.get("metrics", {})
        if int(metrics.get("queued_requests", 0)) >= main_module.MAX_QUEUED_REQUESTS:
            reasons.append("queue_pressure")
    if reasons:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reasons": reasons,
                "ready_models": ready_models,
                **main_module._make_error_detail(
                    "Readiness checks failed",
                    error_type="service_unavailable_error",
                    request_id=request_id,
                    code="not_ready",
                ),
            },
        )
    return {
        "status": "ready",
        "ready_models": ready_models,
        "backend_count": 1,
    }


@router.get("/health/detailed")
async def health_detailed():
    main_module = _main_module()
    try:
        models = await main_module._load_models_from_compute(force_refresh=True)
        backend = await main_module._backend_snapshot(force_refresh=False)
        return {
            "status": "healthy" if backend.get("healthy", False) else "degraded",
            "api_server": {"healthy": True},
            "compute_backend": {
                "mode": "single-node",
                "grpc_target": main_module.GRPC_SERVER_ADDRESS,
                "healthy": backend.get("healthy", False),
                "ready_models": [model["id"] for model in models if bool(model.get("_ready", False))],
                "backend_count": 1,
                "model_count": len(models),
                "backends": [backend],
            },
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "api_server": {"healthy": True},
            "compute_backend": {
                "healthy": False,
                "error": str(exc),
            },
        }
