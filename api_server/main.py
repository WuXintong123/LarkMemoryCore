# ===- main.py -----------------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# FastAPI Service, providing OpenAI API compatible interface
#
# ===---------------------------------------------------------------------------

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from .app import app, create_app
from .core.config import (
    ADMIN_ENDPOINTS,
    API_BIND_HOST,
    API_BIND_PORT,
    API_KEY,
    API_KEY_ALLOWED_MODELS,
    API_KEY_ID,
    API_KEY_SCOPES,
    API_KEYS_FILE,
    API_KEYS_JSON,
    APP_VERSION,
    COMPLETION_PROMPT_LIST_CONCURRENCY,
    CORS_ALLOW_ORIGINS,
    GRACEFUL_SHUTDOWN_TIMEOUT_S,
    GRPC_SERVER_ADDRESS,
    GRPC_TIMEOUT,
    MAX_CONTENT_LENGTH,
    MAX_QUEUED_REQUESTS,
    MODEL_CACHE_TTL_S,
    MODEL_LIST_TIMEOUT_S,
    MODELS_CONFIG_FILE,
    OPENAI_DOC_PATH,
    OPENAPI_SCHEMA_PATH,
    PARTIAL_REASON_VALUES,
    PUBLIC_ENDPOINTS,
    RATE_LIMIT_BURST,
    RATE_LIMIT_RPM,
    RATE_LIMIT_SCOPE,
    RATE_LIMIT_TTL_S,
    LARK_MEMORY_CORE_MEMORY_DB_PATH,
    LARK_MEMORY_CORE_MEMORY_ENGINE_ENABLED,
    LARK_MEMORY_CORE_MEMORY_MAX_CARDS,
    STARTUP_REQUIRE_COMPUTE,
    VALID_ROLES,
    _env_bool,
    _parse_cors_allow_origins,
    _safe_env_int,
)
from .core.errors import (
    _backend_request_id,
    _ensure_request_id,
    _error_response,
    _error_type_for_status,
    _make_error_detail,
    _merge_headers,
    _normalize_error_detail,
    _partial_reason,
    _request_base_url,
    _request_id_from_request,
    _validation_error_message,
    openai_http_exception_handler,
    openai_unhandled_exception_handler,
    openai_validation_exception_handler,
)
from .core.lifecycle import GracefulShutdownHandler, lifespan, shutdown_handler
from .core.middleware import GracefulShutdownMiddleware, RequestLoggingMiddleware
from .core.rate_limit import (
    RateLimiter,
    TokenBucket,
    _normalize_scope_config,
    rate_limit_scopes,
    rate_limiter,
)
from .dependencies.auth import (
    ApiKeyAuthManager,
    ApiKeyPrincipal,
    _parse_authorization_header,
    require_api_scopes,
    resolve_models_principal,
    verify_api_key,
)
from .dependencies.guards import check_rate_limit
from .domain.chat_template import ChatTemplate, format_buddy_deepseek_r1_messages
from .domain.model_policy import (
    ModelServingPolicy,
    SUPPORTED_INFERENCE_PARAMETERS,
    UNSUPPORTED_INFERENCE_PARAMETERS,
    filter_anonymous_models,
    public_model_dict,
)
from .infra.grpc_client import ComputeClient
from .schemas.common import Message
from .schemas.requests import (
    CancelRequest,
    ChatCompletionRequest,
    CompletionRequest,
    RegisterModelRequest,
)
from .schemas.responses import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionResponse,
    Usage,
)
from .services.backend_service import (
    BackendTarget,
    _backend_snapshot,
    _load_backend_health,
    _select_backend,
    _to_thread,
)
from .services.inference_service import (
    _completion_finish_reason,
    _create_stream_chunk,
    _create_stream_error_event,
    _grpc_deadline_seconds,
    _log_prompt_trace,
    _next_stream_content,
    _normalize_completion_prompts,
    _prompt_trace_enabled,
    _raise_compute_http_error,
    _raise_unsupported_param_error,
    _raw_request_body_for_trace,
    _resolve_request_timeout_ms,
    _stream_chat_response,
    _stream_completion_response,
    _prepare_messages_for_prompt,
    _latest_user_message_for_compute,
    _validate_model_policy_for_endpoint,
    _validate_unsupported_openai_params,
    _build_prompt_from_messages,
    validate_chat_request,
    validate_completion_request,
    logger,
)
from .services.metrics_service import (
    _admin_metrics_payload,
    _increment_api_metric,
    _load_metrics_snapshot,
    _prometheus_text,
)
from .services.memory_service import DecisionMemoryEngine
from .services.model_service import (
    _ensure_model_available,
    _get_model_policy,
    _get_model_record,
    _get_model_record_or_default,
    _invalidate_model_cache,
    _load_models_from_compute,
    _model_cache,
    _model_exists,
    _model_info_to_record,
)


compute_client = ComputeClient(GRPC_SERVER_ADDRESS)
chat_template = ChatTemplate()
memory_engine = DecisionMemoryEngine.from_env(
    enabled=LARK_MEMORY_CORE_MEMORY_ENGINE_ENABLED,
    db_path=LARK_MEMORY_CORE_MEMORY_DB_PATH,
    max_cards=LARK_MEMORY_CORE_MEMORY_MAX_CARDS,
)
auth_manager = ApiKeyAuthManager.from_config(
    legacy_api_key=API_KEY,
    legacy_key_id=API_KEY_ID,
    legacy_scopes=API_KEY_SCOPES,
    legacy_allowed_models=API_KEY_ALLOWED_MODELS,
    api_keys_file=API_KEYS_FILE,
    api_keys_json=API_KEYS_JSON,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=API_BIND_HOST, port=API_BIND_PORT)
