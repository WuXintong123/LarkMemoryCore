"""Admin and metrics routes."""

from __future__ import annotations

from importlib import import_module
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from ..dependencies.auth import ApiKeyPrincipal, require_api_scopes
from ..schemas.requests import CancelRequest, RegisterModelRequest


router = APIRouter()


def _main_module():
    return import_module("api_server.main")


@router.post("/v1/admin/models/register")
async def register_model(
    request: RegisterModelRequest,
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    raise HTTPException(
        status_code=501,
        detail={
            "error": {
                "message": (
                    "Model registration via API is not supported. "
                    "Update models.json and then call POST /v1/admin/reload-models."
                ),
                "type": "not_supported_error",
            }
        },
    )


@router.delete("/v1/admin/models/{model_id:path}")
async def unregister_model(
    model_id: str,
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    raise HTTPException(
        status_code=501,
        detail={
            "error": {
                "message": (
                    "Model unregistration via API is not supported. "
                    "Update models.json and then call POST /v1/admin/reload-models."
                ),
                "type": "not_supported_error",
            }
        },
    )


@router.get("/v1/admin/metrics")
async def get_metrics(_: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin"))):
    main_module = _main_module()
    try:
        metrics = await main_module._load_metrics_snapshot()
        return main_module._admin_metrics_payload(metrics)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get metrics: {exc}")


@router.get("/v1/admin/backends")
async def get_backends(
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    main_module = _main_module()
    try:
        backend = await main_module._backend_snapshot(force_refresh=True)
        return {
            "mode": "single-node",
            "source_path": main_module.GRPC_SERVER_ADDRESS,
            "backend_count": 1,
            "data": [backend],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch backends: {exc}")


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    main_module = _main_module()
    try:
        metrics = await main_module._load_metrics_snapshot()
        return PlainTextResponse(
            main_module._prometheus_text(metrics),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to render metrics: {exc}")


@router.post("/v1/admin/cancel")
async def cancel_request(
    request: CancelRequest,
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    main_module = _main_module()
    try:
        success = await main_module._to_thread(
            main_module.compute_client.cancel_request,
            request.request_id,
            5.0,
        )
        return {
            "success": success,
            "message": f"Request '{request.request_id}' cancellation "
            + ("successful" if success else "failed"),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v1/admin/reload-models")
async def reload_models(
    _: Optional[ApiKeyPrincipal] = Depends(require_api_scopes("admin")),
):
    main_module = _main_module()
    try:
        reload_result = await main_module._to_thread(main_module.compute_client.reload_models, 10.0)
        await main_module._invalidate_model_cache()
        models = await main_module._load_models_from_compute(force_refresh=True)
        return {
            **reload_result,
            "models": [main_module.public_model_dict(model) for model in models],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reload models: {exc}")
