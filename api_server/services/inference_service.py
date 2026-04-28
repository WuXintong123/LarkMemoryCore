"""Inference validation and orchestration helpers."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from importlib import import_module
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

import grpc
from fastapi import HTTPException, Request

from ..core.config import (
    COMPLETION_PROMPT_LIST_CONCURRENCY,
    GRPC_TIMEOUT,
    MAX_CONTENT_LENGTH,
    VALID_ROLES,
    _env_bool,
)
from ..core.errors import (
    _backend_request_id,
    _make_error_detail,
    _partial_reason,
    _request_id_from_request,
)
from ..domain.model_policy import ModelServingPolicy
from ..infra.logger import setup_logger
from ..schemas.common import Message
from ..schemas.requests import ChatCompletionRequest, CompletionRequest


logger = setup_logger("api_server")
_STREAM_EOF = object()
PROMPT_TRACE_ENV = "LARK_MEMORY_CORE_DEBUG_PROMPT_IO"
OPENCLAW_FEISHU_METADATA_PREFIX_RE = re.compile(
    r"^\s*Conversation info \(untrusted metadata\):\s*```json\s*.*?```\s*",
    re.DOTALL,
)
OPENCLAW_FEISHU_TRANSPORT_PREFIX_RE = re.compile(
    r"^\s*System:\s*\[\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?\s*"
    r"(?:GMT|UTC)[+-]\d{1,2}(?::\d{2})?\]\s*(?:Feishu|Lark)\[[^\]]+\]"
    r"[^\n]*?\[msg:[^\]]+\]\s*",
    re.IGNORECASE,
)
OPENCLAW_FEISHU_SENDER_PREFIX_RE = re.compile(
    r"^\s*Sender \(untrusted metadata\):\s*```json\s*.*?```\s*",
    re.DOTALL,
)
OPENCLAW_MESSAGE_ID_LINE_RE = re.compile(r"^\[message_id:[^\]]+\]\s*$", re.IGNORECASE)
OPENCLAW_SENDER_LINE_PREFIX_RE = re.compile(r"^[^:\n]{1,256}:\s*")
OPENCLAW_SYSTEM_HINT_LINE_RE = re.compile(
    r"^\[System: (?:The content may include mention tags.*|If user_id is .*that mention refers to you\.)\]$"
)
OPENCLAW_TIMEZONE_PREFIX_RE = re.compile(
    r"^(?:\[\s*)?\d{1,2}(?::\d{1,2}){0,2}\s*(?:GMT|UTC)[+-]\d{1,2}(?::\d{2})?\]?\s*",
    re.IGNORECASE,
)
OPENCLAW_STRUCTURED_MENTION_PREFIX_RE = re.compile(
    r"^(?:<at\b[^>]*>.*?</at>\s*)+",
    re.IGNORECASE | re.DOTALL,
)
OPENCLAW_INLINE_BOT_MENTION_RE = re.compile(
    r"^@[^\s]*bot[^\s]*\b[:：,，]?\s*",
    re.IGNORECASE,
)
OPENCLAW_MULTIWORD_BOT_MENTION_RE = re.compile(
    r"^@[\w.-]+(?:\s+[\w.-]+){0,3}\s+bot\b[:：,，]?\s*",
    re.IGNORECASE,
)
OPENCLAW_VISIBLE_BOT_NAME_RE = re.compile(
    r"^(?:[\w.-]+\s+){0,3}bot\b[:：,，]?\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptMessage:
    role: str
    content: str


def _main_module():
    return import_module("api_server.main")


def _prompt_trace_enabled() -> bool:
    return _env_bool(PROMPT_TRACE_ENV, False)


def _log_prompt_trace(
    message: str,
    *,
    request_id: str,
    model_id: str,
    request_kind: str,
    backend_model_id: Optional[str] = None,
    stream: Optional[bool] = None,
    raw_request: Optional[str] = None,
    prompt: Optional[str] = None,
    result: Optional[str] = None,
    completion_status: Optional[str] = None,
    completion_detail: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    if not _prompt_trace_enabled():
        return

    extra: Dict[str, Any] = {
        "component": "api_server",
        "request_id": request_id,
        "model_id": model_id,
        "request_kind": request_kind,
    }
    if backend_model_id:
        extra["backend_model_id"] = backend_model_id
    if stream is not None:
        extra["stream"] = stream
    if raw_request is not None:
        extra["raw_request"] = raw_request
        extra["raw_request_chars"] = len(raw_request)
    if prompt is not None:
        extra["prompt"] = prompt
        extra["prompt_chars"] = len(prompt)
    if result is not None:
        extra["result"] = result
        extra["result_chars"] = len(result)
    if completion_status:
        extra["completion_status"] = completion_status
    if completion_detail:
        extra["completion_detail"] = completion_detail
    if error_message:
        extra["error_message"] = error_message

    logger.info(message, extra=extra)


async def _raw_request_body_for_trace(http_request: Request) -> str:
    raw_body = await http_request.body()
    if not raw_body:
        return ""
    return raw_body.decode("utf-8", errors="replace")


def _raise_unsupported_param_error(param_name: str, endpoint: str) -> None:
    raise HTTPException(
        status_code=400,
        detail=_make_error_detail(
            f"Parameter '{param_name}' is not supported for {endpoint}",
            error_type="invalid_request_error",
            param=param_name,
            code="unsupported_parameter",
        ),
    )


def _validate_unsupported_openai_params(
    *,
    endpoint: str,
    frequency_penalty: Optional[float],
    presence_penalty: Optional[float],
    stop: Optional[Union[str, List[str]]],
) -> None:
    if frequency_penalty is not None:
        _raise_unsupported_param_error("frequency_penalty", endpoint)
    if presence_penalty is not None:
        _raise_unsupported_param_error("presence_penalty", endpoint)
    if stop is not None:
        _raise_unsupported_param_error("stop", endpoint)


def _normalize_completion_prompts(prompt: Union[str, List[str]]) -> List[str]:
    if isinstance(prompt, str):
        return [prompt]
    return prompt


def _message_prompt_content(message: Message) -> str:
    rendered_content = message.content

    if message.tool_calls:
        tool_calls_payload = json.dumps(
            message.tool_calls, ensure_ascii=False, separators=(",", ":")
        )
        tool_calls_line = f"Tool calls: {tool_calls_payload}"
        rendered_content = (
            f"{rendered_content}\n{tool_calls_line}"
            if rendered_content
            else tool_calls_line
        )

    if message.tool_call_id:
        tool_call_id_line = f"Tool call id: {message.tool_call_id}"
        rendered_content = (
            f"{tool_call_id_line}\n{rendered_content}"
            if rendered_content
            else tool_call_id_line
        )

    return rendered_content


def _openclaw_has_mention_context(text: str) -> bool:
    stripped = text.lstrip()
    return (
        stripped.startswith("@")
        or "<at user_id=" in text
        or "[System: The content may include mention tags" in text
        or "that mention refers to you" in text
    )


def _extract_real_user_question(raw_content: str) -> str:
    original = raw_content.strip()
    if not original:
        return original

    cleaned = original
    known_wrapper_detected = False
    mention_context_detected = _openclaw_has_mention_context(cleaned)
    removed_system_hint = False
    previous = None

    # Keep stripping wrappers until the payload stabilizes so transport headers,
    # metadata blocks, and sender prefixes can be removed regardless of order.
    while cleaned and cleaned != previous:
        previous = cleaned

        for pattern in (
            OPENCLAW_FEISHU_TRANSPORT_PREFIX_RE,
            OPENCLAW_FEISHU_METADATA_PREFIX_RE,
            OPENCLAW_FEISHU_SENDER_PREFIX_RE,
        ):
            updated = pattern.sub("", cleaned, count=1)
            if updated != cleaned:
                known_wrapper_detected = True
                cleaned = updated.strip()

        normalized_lines: List[str] = []
        removed_wrapper_line = False
        for line in cleaned.splitlines():
            stripped = line.strip()
            if not stripped:
                normalized_lines.append("")
                continue
            if OPENCLAW_MESSAGE_ID_LINE_RE.match(stripped):
                known_wrapper_detected = True
                removed_wrapper_line = True
                continue
            if OPENCLAW_SYSTEM_HINT_LINE_RE.match(stripped):
                known_wrapper_detected = True
                mention_context_detected = True
                removed_system_hint = True
                removed_wrapper_line = True
                continue
            normalized_lines.append(line)

        if removed_wrapper_line:
            cleaned = "\n".join(normalized_lines).strip()

        if cleaned and known_wrapper_detected:
            cleaned = OPENCLAW_TIMEZONE_PREFIX_RE.sub("", cleaned, count=1).strip()
            sender_prefix_removed = OPENCLAW_SENDER_LINE_PREFIX_RE.sub(
                "", cleaned, count=1
            )
            if sender_prefix_removed != cleaned:
                cleaned = sender_prefix_removed.strip()
            cleaned = OPENCLAW_TIMEZONE_PREFIX_RE.sub("", cleaned, count=1).strip()

    if cleaned and mention_context_detected:
        previous = None
        while cleaned and cleaned != previous:
            previous = cleaned
            for pattern in (
                OPENCLAW_STRUCTURED_MENTION_PREFIX_RE,
                OPENCLAW_INLINE_BOT_MENTION_RE,
                OPENCLAW_MULTIWORD_BOT_MENTION_RE,
                OPENCLAW_VISIBLE_BOT_NAME_RE,
            ):
                updated = pattern.sub("", cleaned, count=1).lstrip()
                if updated != cleaned:
                    cleaned = updated
                    break

    cleaned = cleaned.strip()
    if cleaned:
        return cleaned
    if known_wrapper_detected or removed_system_hint:
        return original
    return original


def _is_trailing_assistant_placeholder(message: Message) -> bool:
    return (
        message.role == "assistant"
        and not message.content.strip()
        and not message.tool_calls
    )


def _prepare_messages_for_prompt(messages: List[Message]) -> List[PromptMessage]:
    prompt_messages = list(messages)
    while prompt_messages and _is_trailing_assistant_placeholder(prompt_messages[-1]):
        prompt_messages.pop()

    return [
        PromptMessage(role=message.role, content=_message_prompt_content(message))
        for message in prompt_messages
    ]


def _latest_user_message_for_compute(messages: List[Message]) -> PromptMessage:
    prompt_messages = _prepare_messages_for_prompt(messages)
    for message in reversed(prompt_messages):
        if message.role == "user":
            return PromptMessage(
                role="user",
                content=_extract_real_user_question(message.content),
            )
    raise ValueError("At least one user message is required")


def _resolve_request_timeout_ms(policy: ModelServingPolicy) -> int:
    if policy.request_timeout_ms > 0:
        return policy.request_timeout_ms
    return int(GRPC_TIMEOUT * 1000)


def _grpc_deadline_seconds(request_timeout_ms: int) -> float:
    base = max(float(request_timeout_ms) / 1000.0, 1.0)
    return base + 5.0


def _completion_finish_reason(status: str) -> str:
    if status in {"max_tokens", "partial_timeout"}:
        return "length"
    return "stop"


def _raise_compute_http_error(exc: Exception) -> None:
    if isinstance(exc, grpc.RpcError):
        code = exc.code()
        details = exc.details() if hasattr(exc, "details") else str(exc)
        if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
            raise HTTPException(
                status_code=503,
                detail=_make_error_detail(
                    str(details),
                    error_type="service_unavailable_error",
                    code="compute_saturated",
                ),
            )
        if code == grpc.StatusCode.CANCELLED:
            raise HTTPException(
                status_code=409,
                detail=_make_error_detail(
                    str(details),
                    error_type="cancelled_error",
                    code="request_cancelled",
                ),
            )
        if code == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(
                status_code=404,
                detail=_make_error_detail(
                    str(details),
                    error_type="invalid_request_error",
                    code="model_not_found",
                    param="model",
                ),
            )
        if code == grpc.StatusCode.INVALID_ARGUMENT:
            raise HTTPException(
                status_code=400,
                detail=_make_error_detail(
                    str(details),
                    error_type="invalid_request_error",
                ),
            )
        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            raise HTTPException(
                status_code=504,
                detail=_make_error_detail(
                    str(details),
                    error_type="timeout_error",
                    code="compute_timeout",
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=_make_error_detail(
                str(details),
                error_type="server_error",
                code="compute_rpc_error",
            ),
        )

    message = str(exc)
    if "Request cancelled" in message:
        raise HTTPException(
            status_code=409,
            detail=_make_error_detail(
                message,
                error_type="cancelled_error",
                code="request_cancelled",
            ),
        )
    if "unreachable" in message.lower():
        raise HTTPException(
            status_code=503,
            detail=_make_error_detail(
                message,
                error_type="service_unavailable_error",
                code="compute_unreachable",
            ),
        )
    raise HTTPException(
        status_code=500,
        detail=_make_error_detail(
            f"Error processing request: {message}",
            error_type="server_error",
            code="request_processing_failed",
        ),
    )


def _validate_model_policy_for_endpoint(
    policy: ModelServingPolicy,
    endpoint_kind: str,
    *,
    requested_max_tokens: Optional[int],
    prompt_lengths: List[int],
) -> Optional[int]:
    if not policy.allows_endpoint(endpoint_kind):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": f"Model does not support the {endpoint_kind} endpoint",
                    "type": "invalid_request_error",
                    "param": "model",
                }
            },
        )

    if endpoint_kind == "chat" and policy.prompt_style == "raw_completion":
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Model does not support chat prompt rendering",
                    "type": "invalid_request_error",
                    "param": "model",
                }
            },
        )

    effective_max_tokens = (
        requested_max_tokens
        if requested_max_tokens is not None
        else (policy.default_max_tokens if policy.default_max_tokens > 0 else None)
    )
    if (
        effective_max_tokens is not None
        and policy.max_max_tokens > 0
        and effective_max_tokens > policy.max_max_tokens
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": f"max_tokens exceeds model limit {policy.max_max_tokens}",
                    "type": "invalid_request_error",
                    "param": "max_tokens",
                }
            },
        )

    if policy.max_input_chars > 0:
        longest = max(prompt_lengths) if prompt_lengths else 0
        if longest > policy.max_input_chars:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": f"Input exceeds model limit {policy.max_input_chars} characters",
                        "type": "invalid_request_error",
                        "param": "messages" if endpoint_kind == "chat" else "prompt",
                    }
                },
            )

    return effective_max_tokens


def validate_chat_request(request: ChatCompletionRequest) -> None:
    _validate_unsupported_openai_params(
        endpoint="/v1/chat/completions",
        frequency_penalty=request.frequency_penalty,
        presence_penalty=request.presence_penalty,
        stop=request.stop,
    )

    if not request.messages:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "messages must not be empty", "type": "invalid_request_error"}},
        )

    for msg in request.messages:
        if msg.role not in VALID_ROLES:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": f"Invalid role '{msg.role}'. Valid roles are: {sorted(VALID_ROLES)}",
                        "type": "invalid_request_error",
                    }
                },
            )

    if not any(msg.role == "user" for msg in request.messages):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "At least one user message is required",
                    "type": "invalid_request_error",
                }
            },
        )

    for msg in request.messages:
        if len(msg.content) > MAX_CONTENT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": f"Message content exceeds maximum length of {MAX_CONTENT_LENGTH} characters",
                        "type": "invalid_request_error",
                    }
                },
            )
        if msg.unsupported_content_types:
            unsupported_types = ", ".join(msg.unsupported_content_types)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": (
                            "Only text content parts are supported for chat messages. "
                            f"Received unsupported content types: {unsupported_types}"
                        ),
                        "type": "invalid_request_error",
                        "param": "messages",
                    }
                },
            )


def validate_completion_request(request: CompletionRequest) -> None:
    _validate_unsupported_openai_params(
        endpoint="/v1/completions",
        frequency_penalty=request.frequency_penalty,
        presence_penalty=request.presence_penalty,
        stop=request.stop,
    )

    if isinstance(request.prompt, str) and not request.prompt:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "prompt must not be empty",
                    "type": "invalid_request_error",
                }
            },
        )

    prompts = _normalize_completion_prompts(request.prompt)
    if not prompts:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "prompt list must not be empty",
                    "type": "invalid_request_error",
                }
            },
        )

    for index, item in enumerate(prompts):
        if not isinstance(item, str) or not item:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": f"prompt list element at index {index} must not be empty",
                        "type": "invalid_request_error",
                    }
                },
            )


def _build_prompt_from_messages(
    messages: List[Message],
    policy: ModelServingPolicy,
) -> str:
    main_module = _main_module()
    prompt_messages = [_latest_user_message_for_compute(messages)]
    if policy.prompt_style == "buddy_deepseek_r1":
        return prompt_messages[0].content if prompt_messages else ""
    return main_module.chat_template.format_messages(prompt_messages)


def _create_stream_chunk(
    chunk_id: str,
    model: str,
    content: str,
    finish_reason: Optional[str] = None,
    is_chat: bool = True,
) -> str:
    if is_chat:
        delta = {"role": "assistant", "content": content} if content else {}
        if finish_reason:
            delta = {}
        chunk_data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
    else:
        chunk_data = {
            "id": chunk_id,
            "object": "text_completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "text": content,
                    "finish_reason": finish_reason,
                }
            ],
        }
    return f"data: {json.dumps(chunk_data)}\n\n"


def _create_stream_error_event(
    request_id: str,
    message: str,
    *,
    error_type: str = "server_error",
    param: Optional[str] = None,
    code: Optional[str] = None,
) -> str:
    payload = _make_error_detail(
        message,
        error_type=error_type,
        request_id=request_id,
        param=param,
        code=code,
    )
    return f"data: {json.dumps(payload)}\n\n"


async def _next_stream_content(stream_iter) -> Tuple[bool, Optional[Any]]:
    main_module = _main_module()

    def _next_or_eof(iterator):
        try:
            return next(iterator)
        except StopIteration:
            return _STREAM_EOF

    content = await main_module._to_thread(_next_or_eof, stream_iter)
    if content is _STREAM_EOF:
        return False, None
    return True, content


async def _stream_chat_response(
    request: ChatCompletionRequest,
    prompt: str,
    http_request: Request,
    routed_backend=None,
) -> AsyncGenerator[str, None]:
    main_module = _main_module()
    request_id = _request_id_from_request(http_request) or f"chatcmpl-{int(time.time())}"
    chunk_id = request_id
    stream_iter = None
    emitted_terminal_chunk = False
    collected_chunks: List[str] = []
    if routed_backend is None:
        routed_backend = await main_module._select_backend(request.model)

    try:
        model_record = await main_module._get_model_record(request.model)
        policy = main_module._get_model_policy(model_record)
        request_timeout_ms = main_module._resolve_request_timeout_ms(policy)
        stream_iter = await main_module._to_thread(
            routed_backend.client.process_stream,
            prompt,
            routed_backend.backend_model_id,
            main_module._grpc_deadline_seconds(request_timeout_ms),
            request.temperature,
            request.max_tokens,
            request.top_p,
            request.top_k,
            request.repetition_penalty,
            request.seed,
            request_id,
            request_timeout_ms,
        )
        while True:
            has_chunk, event = await main_module._next_stream_content(stream_iter)
            if not has_chunk:
                break
            content = event.content if hasattr(event, "content") else str(event)
            is_final = bool(getattr(event, "is_final", False))
            error_message = getattr(event, "error_message", "")
            completion_status = getattr(event, "completion_status", "completed")
            if error_message:
                _log_prompt_trace(
                    "API server returning streaming error",
                    request_id=request_id,
                    model_id=request.model,
                    request_kind="chat",
                    backend_model_id=routed_backend.backend_model_id,
                    stream=True,
                    result="".join(collected_chunks),
                    completion_status=completion_status,
                    completion_detail=getattr(event, "completion_detail", ""),
                    error_message=error_message,
                )
                yield _create_stream_error_event(
                    request_id,
                    error_message,
                    error_type="server_error",
                    code=completion_status if completion_status != "completed" else None,
                )
                emitted_terminal_chunk = True
                break
            if content:
                collected_chunks.append(content)
                yield _create_stream_chunk(chunk_id, request.model, content, is_chat=True)
            if is_final:
                _log_prompt_trace(
                    "API server returning streaming result",
                    request_id=request_id,
                    model_id=request.model,
                    request_kind="chat",
                    backend_model_id=routed_backend.backend_model_id,
                    stream=True,
                    result="".join(collected_chunks),
                    completion_status=completion_status,
                    completion_detail=getattr(event, "completion_detail", ""),
                )
                yield _create_stream_chunk(
                    chunk_id,
                    request.model,
                    "",
                    finish_reason=main_module._completion_finish_reason(completion_status),
                    is_chat=True,
                )
                yield "data: [DONE]\n\n"
                emitted_terminal_chunk = True
                break

            if await http_request.is_disconnected():
                logger.warning(
                    "Client disconnected during chat streaming, cancelling gRPC stream",
                    extra={"component": "api_server", "chunk_id": chunk_id, "model": request.model},
                )
                await main_module._to_thread(stream_iter.close)
                return

        if not emitted_terminal_chunk:
            _log_prompt_trace(
                "API server returning streaming result",
                request_id=request_id,
                model_id=request.model,
                request_kind="chat",
                backend_model_id=routed_backend.backend_model_id,
                stream=True,
                result="".join(collected_chunks),
                completion_status="completed",
            )
            yield _create_stream_chunk(
                chunk_id,
                request.model,
                "",
                finish_reason="stop",
                is_chat=True,
            )
            yield "data: [DONE]\n\n"

    except grpc.RpcError as exc:
        grpc_code = exc.code()
        grpc_code_name = grpc_code.name.lower() if grpc_code else "unknown"
        grpc_details = exc.details() if hasattr(exc, "details") and exc.details() else str(exc)
        _log_prompt_trace(
            "API server returning gRPC streaming error",
            request_id=request_id,
            model_id=request.model,
            request_kind="chat",
            backend_model_id=routed_backend.backend_model_id if routed_backend else None,
            stream=True,
            result="".join(collected_chunks),
            completion_status=grpc_code_name,
            error_message=grpc_details,
        )
        logger.error(
            "gRPC stream error during chat streaming",
            extra={
                "component": "api_server",
                "chunk_id": chunk_id,
                "model": request.model,
                "grpc_status": grpc_code_name,
                "grpc_details": grpc_details,
            },
        )
        yield _create_stream_error_event(
            request_id,
            f"Stream interrupted: {grpc_details}",
            error_type="server_error",
            code=grpc_code_name,
        )
    except Exception as exc:
        _log_prompt_trace(
            "API server returning streaming error",
            request_id=request_id,
            model_id=request.model,
            request_kind="chat",
            backend_model_id=routed_backend.backend_model_id if routed_backend else None,
            stream=True,
            result="".join(collected_chunks),
            completion_status="server_error",
            error_message=str(exc),
        )
        yield _create_stream_error_event(request_id, str(exc), error_type="server_error")


async def _stream_completion_response(
    request: CompletionRequest,
    prompt: str,
    http_request: Request,
    routed_backend=None,
) -> AsyncGenerator[str, None]:
    main_module = _main_module()
    request_id = _request_id_from_request(http_request) or f"cmpl-{int(time.time())}"
    chunk_id = request_id
    stream_iter = None
    emitted_terminal_chunk = False
    collected_chunks: List[str] = []
    if routed_backend is None:
        routed_backend = await main_module._select_backend(request.model)

    try:
        model_record = await main_module._get_model_record(request.model)
        policy = main_module._get_model_policy(model_record)
        request_timeout_ms = main_module._resolve_request_timeout_ms(policy)
        stream_iter = await main_module._to_thread(
            routed_backend.client.process_stream,
            prompt,
            routed_backend.backend_model_id,
            main_module._grpc_deadline_seconds(request_timeout_ms),
            request.temperature,
            request.max_tokens,
            request.top_p,
            request.top_k,
            request.repetition_penalty,
            request.seed,
            request_id,
            request_timeout_ms,
        )
        while True:
            has_chunk, event = await main_module._next_stream_content(stream_iter)
            if not has_chunk:
                break
            content = event.content if hasattr(event, "content") else str(event)
            is_final = bool(getattr(event, "is_final", False))
            error_message = getattr(event, "error_message", "")
            completion_status = getattr(event, "completion_status", "completed")
            if error_message:
                _log_prompt_trace(
                    "API server returning streaming error",
                    request_id=request_id,
                    model_id=request.model,
                    request_kind="completion",
                    backend_model_id=routed_backend.backend_model_id,
                    stream=True,
                    result="".join(collected_chunks),
                    completion_status=completion_status,
                    completion_detail=getattr(event, "completion_detail", ""),
                    error_message=error_message,
                )
                yield _create_stream_error_event(
                    request_id,
                    error_message,
                    error_type="server_error",
                    code=completion_status if completion_status != "completed" else None,
                )
                emitted_terminal_chunk = True
                break
            if content:
                collected_chunks.append(content)
                yield _create_stream_chunk(chunk_id, request.model, content, is_chat=False)
            if is_final:
                _log_prompt_trace(
                    "API server returning streaming result",
                    request_id=request_id,
                    model_id=request.model,
                    request_kind="completion",
                    backend_model_id=routed_backend.backend_model_id,
                    stream=True,
                    result="".join(collected_chunks),
                    completion_status=completion_status,
                    completion_detail=getattr(event, "completion_detail", ""),
                )
                yield _create_stream_chunk(
                    chunk_id,
                    request.model,
                    "",
                    finish_reason=main_module._completion_finish_reason(completion_status),
                    is_chat=False,
                )
                yield "data: [DONE]\n\n"
                emitted_terminal_chunk = True
                break

            if await http_request.is_disconnected():
                logger.warning(
                    "Client disconnected during completion streaming, cancelling gRPC stream",
                    extra={"component": "api_server", "chunk_id": chunk_id, "model": request.model},
                )
                await main_module._to_thread(stream_iter.close)
                return

        if not emitted_terminal_chunk:
            _log_prompt_trace(
                "API server returning streaming result",
                request_id=request_id,
                model_id=request.model,
                request_kind="completion",
                backend_model_id=routed_backend.backend_model_id,
                stream=True,
                result="".join(collected_chunks),
                completion_status="completed",
            )
            yield _create_stream_chunk(
                chunk_id,
                request.model,
                "",
                finish_reason="stop",
                is_chat=False,
            )
            yield "data: [DONE]\n\n"

    except grpc.RpcError as exc:
        grpc_code = exc.code()
        grpc_code_name = grpc_code.name.lower() if grpc_code else "unknown"
        grpc_details = exc.details() if hasattr(exc, "details") and exc.details() else str(exc)
        _log_prompt_trace(
            "API server returning gRPC streaming error",
            request_id=request_id,
            model_id=request.model,
            request_kind="completion",
            backend_model_id=routed_backend.backend_model_id if routed_backend else None,
            stream=True,
            result="".join(collected_chunks),
            completion_status=grpc_code_name,
            error_message=grpc_details,
        )
        logger.error(
            "gRPC stream error during completion streaming",
            extra={
                "component": "api_server",
                "chunk_id": chunk_id,
                "model": request.model,
                "grpc_status": grpc_code_name,
                "grpc_details": grpc_details,
            },
        )
        yield _create_stream_error_event(
            request_id,
            f"Stream interrupted: {grpc_details}",
            error_type="server_error",
            code=grpc_code_name,
        )
    except Exception as exc:
        _log_prompt_trace(
            "API server returning streaming error",
            request_id=request_id,
            model_id=request.model,
            request_kind="completion",
            backend_model_id=routed_backend.backend_model_id if routed_backend else None,
            stream=True,
            result="".join(collected_chunks),
            completion_status="server_error",
            error_message=str(exc),
        )
        yield _create_stream_error_event(request_id, str(exc), error_type="server_error")
