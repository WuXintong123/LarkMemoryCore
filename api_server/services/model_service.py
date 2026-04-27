"""Model-cache and availability helpers."""

from __future__ import annotations

import asyncio
import time
from importlib import import_module
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from ..core.config import MODEL_CACHE_TTL_S, MODEL_LIST_TIMEOUT_S
from ..core.errors import _make_error_detail
from ..domain.model_policy import ModelServingPolicy


def _main_module():
    return import_module("api_server.main")


_model_cache_lock = asyncio.Lock()
_model_cache: Dict[str, Any] = {"expires_at": 0.0, "models": []}


def _model_info_to_record(model_info: Any) -> Dict[str, Any]:
    return {
        "id": model_info.model_id,
        "object": "model",
        "created": model_info.created,
        "owned_by": model_info.owned_by,
        "_ready": bool(model_info.ready),
        "_serving_policy": getattr(model_info, "serving_policy", None) or {},
    }


async def _load_models_from_compute(force_refresh: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    if (
        not force_refresh
        and _model_cache["models"]
        and now < _model_cache["expires_at"]
    ):
        return _model_cache["models"]

    async with _model_cache_lock:
        now = time.time()
        if (
            not force_refresh
            and _model_cache["models"]
            and now < _model_cache["expires_at"]
        ):
            return _model_cache["models"]

        main_module = _main_module()
        models = await main_module._to_thread(
            main_module.compute_client.list_models,
            MODEL_LIST_TIMEOUT_S,
        )
        _model_cache["models"] = [_model_info_to_record(model) for model in models]
        _model_cache["expires_at"] = now + MODEL_CACHE_TTL_S
        return _model_cache["models"]


async def _invalidate_model_cache() -> None:
    async with _model_cache_lock:
        _model_cache["models"] = []
        _model_cache["expires_at"] = 0.0


async def _model_exists(model_id: str) -> bool:
    models = await _main_module()._load_models_from_compute()
    return any(model["id"] == model_id for model in models)


async def _get_model_record(
    model_id: str,
    *,
    force_refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    models = await _main_module()._load_models_from_compute(force_refresh=force_refresh)
    for model in models:
        if model["id"] == model_id:
            return model
    return None


async def _get_model_record_or_default(model_id: str) -> Optional[Dict[str, Any]]:
    try:
        return await _main_module()._get_model_record(model_id)
    except Exception:
        return None


def _get_model_policy(model_record: Optional[Dict[str, Any]]) -> ModelServingPolicy:
    if not model_record:
        return ModelServingPolicy()
    return ModelServingPolicy.from_payload(model_record.get("_serving_policy"))


async def _ensure_model_available(model_id: str) -> None:
    main_module = _main_module()
    if not await main_module._model_exists(model_id):
        models = await main_module._load_models_from_compute(force_refresh=True)
        if not any(model["id"] == model_id for model in models):
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_id}' not found.",
            )

    model_record = await main_module._get_model_record(model_id)
    if model_record and bool(model_record.get("_ready", False)):
        return

    health = await main_module._load_backend_health()
    await main_module._invalidate_model_cache()
    model_record = await main_module._get_model_record(model_id, force_refresh=True)
    if health.healthy and model_record and bool(model_record.get("_ready", False)):
        return

    raise HTTPException(
        status_code=503,
        detail=_make_error_detail(
            f"Model '{model_id}' is configured but currently unavailable",
            error_type="service_unavailable_error",
            param="model",
            code="model_unavailable",
        ),
    )
