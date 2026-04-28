"""Configuration values shared across the API server."""

from __future__ import annotations

import os
from typing import List

from ..infra.logger import setup_logger


logger = setup_logger("api_server")

APP_VERSION = "0.2.0"
OPENAI_DOC_PATH = "/docs"
OPENAPI_SCHEMA_PATH = "/openapi.json"
PUBLIC_ENDPOINTS = (
    "/",
    "/health",
    "/ready",
    "/health/detailed",
    "/v1/models",
    "/v1/models/{model_id}",
    "/v1/chat/completions",
    "/v1/completions",
)
ADMIN_ENDPOINTS = (
    "/v1/admin/metrics",
    "/v1/admin/backends",
    "/v1/admin/cancel",
    "/v1/admin/reload-models",
    "/v1/memory/events",
    "/v1/memory/search",
    "/v1/memory/report",
    "/metrics",
)
PARTIAL_REASON_VALUES = {
    "partial_timeout": "partial_timeout",
    "watchdog_timeout": "watchdog_timeout",
    "idle_timeout": "idle_timeout",
    "cancelled": "cancelled",
    "queue_timeout": "queue_timeout",
}


def _safe_env_int(name: str, default: int, *, min_value: int = 1) -> int:
    """Parse integer env var defensively, fallback to default on invalid values."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid integer env var, fallback to default",
            extra={
                "component": "api_server",
                "env_name": name,
                "env_value": raw,
                "default_value": default,
            },
        )
        return default
    if value < min_value:
        logger.warning(
            "Env var below minimum, clamped to min_value",
            extra={
                "component": "api_server",
                "env_name": name,
                "env_value": raw,
                "min_value": min_value,
            },
        )
        return min_value
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_cors_allow_origins(raw_value: str) -> List[str]:
    return [segment.strip() for segment in raw_value.split(",") if segment.strip()]


GRPC_SERVER_ADDRESS = os.getenv("GRPC_SERVER_ADDRESS", "localhost:9000")
GRPC_TIMEOUT = float(os.getenv("GRPC_TIMEOUT", "600"))
MODELS_CONFIG_FILE = os.getenv("MODELS_CONFIG_FILE", "models.json")
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", "32768"))
VALID_ROLES = {"system", "user", "assistant", "tool", "developer"}
API_KEY = os.getenv("API_KEY", "")
API_KEY_ID = os.getenv("API_KEY_ID", "default")
API_KEY_SCOPES = os.getenv("API_KEY_SCOPES", "models:read,inference,admin")
API_KEY_ALLOWED_MODELS = os.getenv("API_KEY_ALLOWED_MODELS", "")
API_KEYS_FILE = os.getenv("API_KEYS_FILE", "")
API_KEYS_JSON = os.getenv("API_KEYS_JSON", "")
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "60"))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", str(max(RATE_LIMIT_RPM, 1))))
RATE_LIMIT_TTL_S = int(os.getenv("RATE_LIMIT_TTL_S", "900"))
RATE_LIMIT_SCOPE = os.getenv("RATE_LIMIT_SCOPE", "api_key,ip,model")
MODEL_CACHE_TTL_S = float(os.getenv("MODEL_CACHE_TTL_S", "3"))
MODEL_LIST_TIMEOUT_S = float(os.getenv("MODEL_LIST_TIMEOUT_S", "5"))
COMPLETION_PROMPT_LIST_CONCURRENCY = _safe_env_int(
    "COMPLETION_PROMPT_LIST_CONCURRENCY",
    3,
    min_value=1,
)
STARTUP_REQUIRE_COMPUTE = _env_bool("STARTUP_REQUIRE_COMPUTE", False)
MAX_QUEUED_REQUESTS = int(os.getenv("MAX_QUEUED_REQUESTS", "0"))
API_BIND_HOST = os.getenv("API_BIND_HOST", "127.0.0.1")
API_BIND_PORT = int(os.getenv("API_BIND_PORT", os.getenv("API_PORT", "8000")))
CORS_ALLOW_ORIGINS = _parse_cors_allow_origins(os.getenv("CORS_ALLOW_ORIGINS", ""))
GRACEFUL_SHUTDOWN_TIMEOUT_S = int(os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT_S", "30"))
LARK_MEMORY_CORE_MEMORY_ENGINE_ENABLED = _env_bool("LARK_MEMORY_CORE_MEMORY_ENGINE_ENABLED", False)
LARK_MEMORY_CORE_MEMORY_DB_PATH = os.getenv(
    "LARK_MEMORY_CORE_MEMORY_DB_PATH",
    os.path.join(".run", "memory-engine", "decision_memory.sqlite3"),
)
LARK_MEMORY_CORE_MEMORY_MAX_CARDS = _safe_env_int("LARK_MEMORY_CORE_MEMORY_MAX_CARDS", 3, min_value=1)
