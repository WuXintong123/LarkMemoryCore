"""Model discovery routes."""

from __future__ import annotations

from importlib import import_module
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies.auth import ApiKeyPrincipal, resolve_models_principal


router = APIRouter()


def _main_module():
    return import_module("api_server.main")


@router.get("/v1/models")
async def list_models(
    principal: Optional[ApiKeyPrincipal] = Depends(resolve_models_principal),
):
    main_module = _main_module()
    try:
        models = await main_module._load_models_from_compute()
        if main_module.auth_manager.enabled and principal is None:
            anonymous_models = main_module.filter_anonymous_models(models)
            if not anonymous_models:
                raise HTTPException(
                    status_code=401,
                    detail="Missing Authorization header",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return {
                "object": "list",
                "data": [main_module.public_model_dict(model) for model in anonymous_models],
            }

        main_module.auth_manager.ensure_scopes(principal, ("models:read", "inference", "admin"))
        models = main_module.auth_manager.filter_models_for_principal(principal, models)
        return {"object": "list", "data": [main_module.public_model_dict(model) for model in models]}
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch models from compute server: {exc}",
        )


@router.get("/v1/models/{model_id:path}")
async def get_model(
    model_id: str,
    principal: Optional[ApiKeyPrincipal] = Depends(resolve_models_principal),
):
    main_module = _main_module()
    model_record = await main_module._get_model_record(model_id)
    if model_record is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    if main_module.auth_manager.enabled and principal is None:
        if not main_module._get_model_policy(model_record).allow_anonymous_models:
            raise HTTPException(
                status_code=401,
                detail="Missing Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return main_module.public_model_dict(model_record)

    main_module.auth_manager.ensure_scopes(principal, ("models:read", "inference", "admin"))
    main_module.auth_manager.ensure_model_access(principal, model_id, conceal_existence=True)
    return main_module.public_model_dict(model_record)
