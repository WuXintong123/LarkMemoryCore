"""Backend-selection and blocking-call helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Dict

from ..core.config import GRPC_SERVER_ADDRESS, MODEL_LIST_TIMEOUT_S


def _main_module():
    return import_module("api_server.main")


@dataclass(frozen=True)
class BackendTarget:
    node_id: str
    public_model_id: str
    backend_model_id: str
    client: Any


async def _to_thread(func, *args, **kwargs):
    """Run blocking code in a worker thread to protect the event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


async def _load_backend_health() -> Any:
    main_module = _main_module()
    return await main_module._to_thread(
        main_module.compute_client.health_check,
        MODEL_LIST_TIMEOUT_S,
    )


async def _backend_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    main_module = _main_module()
    models = await main_module._load_models_from_compute(force_refresh=force_refresh)
    health = await main_module._load_backend_health()
    snapshot: Dict[str, Any] = {
        "id": "default",
        "node_id": "default",
        "grpc_target": GRPC_SERVER_ADDRESS,
        "grpc_address": GRPC_SERVER_ADDRESS,
        "enabled": True,
        "healthy": health.healthy,
        "connected": main_module.compute_client.is_connected,
        "status_message": health.status_message,
        "available_models": [model["id"] for model in models],
        "routes": [
            {
                "model_id": model["id"],
                "backend_model_id": model["id"],
                "weight": 100,
                "enabled": True,
                "ready": bool(model.get("_ready", False)),
            }
            for model in models
        ],
    }
    try:
        metrics = await main_module._load_metrics_snapshot()
        snapshot["metrics"] = {
            "total_requests": metrics.total_requests,
            "successful_requests": metrics.successful_requests,
            "failed_requests": metrics.failed_requests,
            "total_tokens_processed": metrics.total_tokens_processed,
            "average_latency_ms": metrics.average_latency_ms,
            "average_tokens_per_second": metrics.average_tokens_per_second,
            "rejected_requests": metrics.rejected_requests,
            "queued_requests": metrics.queued_requests,
            "active_compute_slots": metrics.active_compute_slots,
            "max_compute_slots": metrics.max_compute_slots,
            "overload_rejections": metrics.overload_rejections,
            "watchdog_timeouts": metrics.watchdog_timeouts,
            "partial_timeout_returns": metrics.partial_timeout_returns,
            "request_cancellations": metrics.request_cancellations,
        }
    except Exception:
        snapshot["metrics"] = {}
    return snapshot


async def _select_backend(model_id: str) -> BackendTarget:
    main_module = _main_module()
    return BackendTarget(
        node_id="default",
        public_model_id=model_id,
        backend_model_id=model_id,
        client=main_module.compute_client,
    )
