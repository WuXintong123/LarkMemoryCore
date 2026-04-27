"""Admin metrics helpers."""

from __future__ import annotations

import threading
from collections import defaultdict
from importlib import import_module
from typing import Any, Dict

from ..core.config import APP_VERSION, MODEL_LIST_TIMEOUT_S


def _main_module():
    return import_module("api_server.main")


_api_metrics_lock = threading.Lock()
_api_metrics: Dict[str, int] = defaultdict(int)


def _increment_api_metric(name: str) -> None:
    with _api_metrics_lock:
        _api_metrics[name] += 1


async def _load_metrics_snapshot() -> Any:
    main_module = _main_module()
    metrics = await main_module._to_thread(
        main_module.compute_client.get_metrics,
        MODEL_LIST_TIMEOUT_S,
    )
    health = await main_module._load_backend_health()
    setattr(
        metrics,
        "node_metrics",
        {
            "default": {
                "healthy": health.healthy,
                "active_compute_slots": metrics.active_compute_slots,
                "queued_requests": metrics.queued_requests,
                "failure_count": 0 if health.healthy else 1,
                "route_hits": metrics.total_requests,
            }
        },
    )
    return metrics


def _admin_metrics_payload(metrics: Any) -> Dict[str, Any]:
    return {
        "total_requests": metrics.total_requests,
        "successful_requests": metrics.successful_requests,
        "failed_requests": metrics.failed_requests,
        "total_tokens_processed": metrics.total_tokens_processed,
        "average_latency_ms": metrics.average_latency_ms,
        "average_tokens_per_second": metrics.average_tokens_per_second,
        "model_metrics": metrics.model_metrics,
        "rejected_requests": getattr(metrics, "rejected_requests", 0),
        "queued_requests": getattr(metrics, "queued_requests", 0),
        "active_compute_slots": getattr(metrics, "active_compute_slots", 0),
        "max_compute_slots": getattr(metrics, "max_compute_slots", 0),
        "overload_rejections": getattr(metrics, "overload_rejections", 0),
        "watchdog_timeouts": getattr(metrics, "watchdog_timeouts", 0),
        "partial_timeout_returns": getattr(metrics, "partial_timeout_returns", 0),
        "request_cancellations": getattr(metrics, "request_cancellations", 0),
        "node_metrics": getattr(metrics, "node_metrics", {}),
        "auth_failures": _api_metrics.get("auth_failures", 0),
    }


def _prometheus_text(metrics: Any) -> str:
    lines = [
        "# HELP ruyi_build_info Build information.",
        "# TYPE ruyi_build_info gauge",
        f'ruyi_build_info{{version="{APP_VERSION}"}} 1',
        "# HELP ruyi_total_requests Total requests handled by the service.",
        "# TYPE ruyi_total_requests counter",
        f"ruyi_total_requests {metrics.total_requests}",
        "# HELP ruyi_successful_requests Successful requests handled by the service.",
        "# TYPE ruyi_successful_requests counter",
        f"ruyi_successful_requests {metrics.successful_requests}",
        "# HELP ruyi_failed_requests Failed requests handled by the service.",
        "# TYPE ruyi_failed_requests counter",
        f"ruyi_failed_requests {metrics.failed_requests}",
        "# HELP ruyi_rejected_requests Rejected requests due to saturation or overload.",
        "# TYPE ruyi_rejected_requests counter",
        f"ruyi_rejected_requests {getattr(metrics, 'rejected_requests', 0)}",
        "# HELP ruyi_queued_requests Current number of queued requests.",
        "# TYPE ruyi_queued_requests gauge",
        f"ruyi_queued_requests {getattr(metrics, 'queued_requests', 0)}",
        "# HELP ruyi_compute_active_slots Current active compute slots.",
        "# TYPE ruyi_compute_active_slots gauge",
        f"ruyi_compute_active_slots {getattr(metrics, 'active_compute_slots', 0)}",
        "# HELP ruyi_compute_max_slots Maximum compute slots.",
        "# TYPE ruyi_compute_max_slots gauge",
        f"ruyi_compute_max_slots {getattr(metrics, 'max_compute_slots', 0)}",
        "# HELP ruyi_average_latency_ms Average request latency in milliseconds.",
        "# TYPE ruyi_average_latency_ms gauge",
        f"ruyi_average_latency_ms {metrics.average_latency_ms}",
        "# HELP ruyi_average_tokens_per_second Average tokens generated per second.",
        "# TYPE ruyi_average_tokens_per_second gauge",
        f"ruyi_average_tokens_per_second {metrics.average_tokens_per_second}",
        "# HELP ruyi_watchdog_timeouts Watchdog timeout count.",
        "# TYPE ruyi_watchdog_timeouts counter",
        f"ruyi_watchdog_timeouts {getattr(metrics, 'watchdog_timeouts', 0)}",
        "# HELP ruyi_partial_timeout_returns Partial timeout return count.",
        "# TYPE ruyi_partial_timeout_returns counter",
        f"ruyi_partial_timeout_returns {getattr(metrics, 'partial_timeout_returns', 0)}",
        "# HELP ruyi_request_cancellations Cancelled request count.",
        "# TYPE ruyi_request_cancellations counter",
        f"ruyi_request_cancellations {getattr(metrics, 'request_cancellations', 0)}",
        "# HELP ruyi_auth_failures Authentication failures seen by the API layer.",
        "# TYPE ruyi_auth_failures counter",
        f"ruyi_auth_failures {_api_metrics.get('auth_failures', 0)}",
        "# HELP ruyi_model_request_count Requests served for each model.",
        "# TYPE ruyi_model_request_count counter",
        "# HELP ruyi_model_total_tokens Total tokens served for each model.",
        "# TYPE ruyi_model_total_tokens counter",
        "# HELP ruyi_model_average_latency_ms Average latency per model in milliseconds.",
        "# TYPE ruyi_model_average_latency_ms gauge",
        "# HELP ruyi_node_healthy Compute backend health (1=healthy, 0=unhealthy).",
        "# TYPE ruyi_node_healthy gauge",
        "# HELP ruyi_node_active_compute_slots Active compute slots per node.",
        "# TYPE ruyi_node_active_compute_slots gauge",
        "# HELP ruyi_node_queued_requests Queued requests per node.",
        "# TYPE ruyi_node_queued_requests gauge",
        "# HELP ruyi_node_failure_count Failure count per node.",
        "# TYPE ruyi_node_failure_count counter",
        "# HELP ruyi_node_route_hits Route selections per node.",
        "# TYPE ruyi_node_route_hits counter",
    ]
    for model_id, model_metrics in sorted(getattr(metrics, "model_metrics", {}).items()):
        escaped_model_id = model_id.replace("\\", "\\\\").replace('"', '\\"')
        lines.extend(
            [
                (
                    f'ruyi_model_request_count{{model="{escaped_model_id}"}} '
                    f"{model_metrics.get('request_count', 0)}"
                ),
                (
                    f'ruyi_model_total_tokens{{model="{escaped_model_id}"}} '
                    f"{model_metrics.get('total_tokens', 0)}"
                ),
                (
                    f'ruyi_model_average_latency_ms{{model="{escaped_model_id}"}} '
                    f"{model_metrics.get('average_latency_ms', 0.0)}"
                ),
            ]
        )
    for node_id, node_metrics in sorted(getattr(metrics, "node_metrics", {}).items()):
        escaped_node_id = node_id.replace("\\", "\\\\").replace('"', '\\"')
        lines.extend(
            [
                (
                    f'ruyi_node_healthy{{node="{escaped_node_id}"}} '
                    f"{1 if node_metrics.get('healthy') else 0}"
                ),
                (
                    f'ruyi_node_active_compute_slots{{node="{escaped_node_id}"}} '
                    f"{node_metrics.get('active_compute_slots', 0)}"
                ),
                (
                    f'ruyi_node_queued_requests{{node="{escaped_node_id}"}} '
                    f"{node_metrics.get('queued_requests', 0)}"
                ),
                (
                    f'ruyi_node_failure_count{{node="{escaped_node_id}"}} '
                    f"{node_metrics.get('failure_count', 0)}"
                ),
                (
                    f'ruyi_node_route_hits{{node="{escaped_node_id}"}} '
                    f"{node_metrics.get('route_hits', 0)}"
                ),
            ]
        )
    return "\n".join(lines) + "\n"
