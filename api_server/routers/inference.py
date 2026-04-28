"""Inference routes."""

from __future__ import annotations

import asyncio
import time
from importlib import import_module
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ..dependencies.auth import ApiKeyPrincipal, require_api_scopes
from ..dependencies.guards import check_rate_limit
from ..schemas.common import Message
from ..schemas.requests import ChatCompletionRequest, CompletionRequest
from ..schemas.responses import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    CompletionChoice,
    CompletionResponse,
    Usage,
)


router = APIRouter()


def _main_module():
    return import_module("api_server.main")


@router.post("/v1/chat/completions")
async def create_chat_completion(
    http_request: Request,
    http_response: Response,
    request: ChatCompletionRequest,
    principal: Optional[ApiKeyPrincipal] = Depends(
        require_api_scopes("inference", "admin")
    ),
    _rate: None = Depends(check_rate_limit),
):
    main_module = _main_module()
    request_id = main_module._ensure_request_id(http_request)
    raw_request_body = await main_module._raw_request_body_for_trace(http_request)
    main_module._log_prompt_trace(
        "API server received raw request",
        request_id=request_id,
        model_id=request.model,
        request_kind="chat",
        stream=bool(request.stream),
        raw_request=raw_request_body,
    )

    main_module.validate_chat_request(request)

    main_module.auth_manager.ensure_model_access(principal, request.model)
    await main_module._ensure_model_available(request.model)
    model_record = await main_module._get_model_record(request.model)
    policy = main_module._get_model_policy(model_record)
    prompt_messages = [main_module._latest_user_message_for_compute(request.messages)]
    request.max_tokens = main_module._validate_model_policy_for_endpoint(
        policy,
        "chat",
        requested_max_tokens=request.max_tokens,
        prompt_lengths=[len(msg.content) for msg in prompt_messages],
    )

    try:
        prompt = main_module._build_prompt_from_messages(request.messages, policy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    memory_headers = {}
    if main_module.memory_engine.enabled:
        try:
            main_module.memory_engine.ingest_chat_messages(
                raw_request_body=raw_request_body,
                messages=request.messages,
                request_id=request_id,
            )
            memory_scope = main_module.memory_engine.chat_scope(raw_request_body)
            memory_results = main_module.memory_engine.search(
                tenant_id=memory_scope["tenant_id"],
                project_id=memory_scope["project_id"],
                conversation_id=memory_scope["conversation_id"],
                query=prompt,
                limit=main_module.LARK_MEMORY_CORE_MEMORY_MAX_CARDS,
                request_id=request_id,
                used_for_prompt=True,
            )
            memory_composition = main_module.memory_engine.compose_prompt(
                prompt,
                memory_results.cards,
            )
            if memory_composition.hit_count > 0:
                prompt = memory_composition.prompt
                main_module.memory_engine.record_prompt_usage(
                    request_id=request_id,
                    tenant_id=memory_scope["tenant_id"],
                    project_id=memory_scope["project_id"],
                    conversation_id=memory_scope["conversation_id"],
                    query=memory_results.query,
                    hit_count=memory_composition.hit_count,
                    top_memory_id=memory_composition.memory_ids[0],
                    injected_chars=memory_composition.injected_characters,
                )
            memory_headers = {
                "X-LarkMemoryCore-Memory-Hit-Count": str(memory_composition.hit_count),
                "X-LarkMemoryCore-Memory-Ids": ",".join(memory_composition.memory_ids),
            }
        except Exception as exc:
            main_module.logger.warning(
                "Memory engine failed during chat prompt preparation: %s",
                str(exc),
                extra={
                    "component": "memory_engine",
                    "request_id": request_id,
                    "model_id": request.model,
                },
            )
            memory_headers = {"X-LarkMemoryCore-Memory-Hit-Count": "0"}

    main_module._validate_model_policy_for_endpoint(
        policy,
        "chat",
        requested_max_tokens=request.max_tokens,
        prompt_lengths=[len(prompt)],
    )
    request_timeout_ms = main_module._resolve_request_timeout_ms(policy)
    grpc_deadline = main_module._grpc_deadline_seconds(request_timeout_ms)
    routed_backend = await main_module._select_backend(request.model)
    main_module._log_prompt_trace(
        "API server received prompt",
        request_id=request_id,
        model_id=request.model,
        request_kind="chat",
        backend_model_id=routed_backend.backend_model_id,
        stream=bool(request.stream),
        prompt=prompt,
    )

    if request.stream:
        return StreamingResponse(
            main_module._stream_chat_response(request, prompt, http_request, routed_backend),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                **memory_headers,
            },
        )

    try:
        result = await main_module._to_thread(
            routed_backend.client.process_with_stats,
            prompt,
            routed_backend.backend_model_id,
            grpc_deadline,
            request.temperature,
            request.max_tokens,
            request.top_p,
            request.top_k,
            request.repetition_penalty,
            request.seed,
            request_id,
            request_timeout_ms,
        )
    except Exception as exc:
        main_module._raise_compute_http_error(exc)

    if result.usage:
        usage = Usage(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.prompt_tokens + result.usage.completion_tokens,
        )
    else:
        usage = Usage(
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(result.output.split()),
            total_tokens=len(prompt.split()) + len(result.output.split()),
        )

    response = ChatCompletionResponse(
        id=result.request_id or request_id or f"chatcmpl-{int(time.time())}",
        created=int(time.time()),
        model=request.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=Message(role="assistant", content=result.output),
                finish_reason=main_module._completion_finish_reason(
                    getattr(result, "completion_status", "completed")
                ),
            )
        ],
        usage=usage,
    )
    partial_reason = main_module._partial_reason(
        getattr(result, "completion_status", "completed"),
        getattr(result, "completion_detail", ""),
    )
    main_module._log_prompt_trace(
        "API server returning result",
        request_id=result.request_id or request_id,
        model_id=request.model,
        request_kind="chat",
        backend_model_id=routed_backend.backend_model_id,
        stream=False,
        result=result.output,
        completion_status=getattr(result, "completion_status", "completed"),
        completion_detail=getattr(result, "completion_detail", ""),
    )
    if partial_reason:
        http_response.headers["X-LarkMemoryCore-Partial-Reason"] = partial_reason
    for header_name, header_value in memory_headers.items():
        http_response.headers[header_name] = header_value
    return response


@router.post("/v1/completions")
async def create_completion(
    http_request: Request,
    http_response: Response,
    request: CompletionRequest,
    principal: Optional[ApiKeyPrincipal] = Depends(
        require_api_scopes("inference", "admin")
    ),
    _rate: None = Depends(check_rate_limit),
):
    main_module = _main_module()
    request_id = main_module._ensure_request_id(http_request)
    raw_request_body = await main_module._raw_request_body_for_trace(http_request)
    main_module._log_prompt_trace(
        "API server received raw request",
        request_id=request_id,
        model_id=request.model,
        request_kind="completion",
        stream=bool(request.stream),
        raw_request=raw_request_body,
    )

    main_module.validate_completion_request(request)

    main_module.auth_manager.ensure_model_access(principal, request.model)
    await main_module._ensure_model_available(request.model)
    model_record = await main_module._get_model_record(request.model)
    policy = main_module._get_model_policy(model_record)

    prompts = main_module._normalize_completion_prompts(request.prompt)
    request.max_tokens = main_module._validate_model_policy_for_endpoint(
        policy,
        "completion",
        requested_max_tokens=request.max_tokens,
        prompt_lengths=[len(prompt) for prompt in prompts],
    )
    request_timeout_ms = main_module._resolve_request_timeout_ms(policy)
    grpc_deadline = main_module._grpc_deadline_seconds(request_timeout_ms)

    if request.stream:
        if len(prompts) != 1:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": "Streaming completions support exactly one prompt",
                        "type": "invalid_request_error",
                        "param": "prompt",
                        "code": "unsupported_parameter",
                    }
                },
            )

        prompt = prompts[0]
        routed_backend = await main_module._select_backend(request.model)
        main_module._log_prompt_trace(
            "API server received prompt",
            request_id=request_id,
            model_id=request.model,
            request_kind="completion",
            backend_model_id=routed_backend.backend_model_id,
            stream=True,
            prompt=prompt,
        )
        return StreamingResponse(
            main_module._stream_completion_response(
                request,
                prompt,
                http_request,
                routed_backend,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    routed_backend = await main_module._select_backend(request.model)

    async def _process_one_prompt(index: int, prompt_text: str):
        try:
            child_request_id = main_module._backend_request_id(request_id, str(index))
            main_module._log_prompt_trace(
                "API server received prompt",
                request_id=child_request_id,
                model_id=request.model,
                request_kind="completion",
                backend_model_id=routed_backend.backend_model_id,
                stream=False,
                prompt=prompt_text,
            )
            result = await main_module._to_thread(
                routed_backend.client.process_with_stats,
                prompt_text,
                routed_backend.backend_model_id,
                grpc_deadline,
                request.temperature,
                request.max_tokens,
                request.top_p,
                request.top_k,
                request.repetition_penalty,
                request.seed,
                child_request_id,
                request_timeout_ms,
            )
            main_module._log_prompt_trace(
                "API server returning result",
                request_id=result.request_id or child_request_id,
                model_id=request.model,
                request_kind="completion",
                backend_model_id=routed_backend.backend_model_id,
                stream=False,
                result=result.output,
                completion_status=getattr(result, "completion_status", "completed"),
                completion_detail=getattr(result, "completion_detail", ""),
            )
            return index, prompt_text, result
        except Exception as exc:
            main_module._raise_compute_http_error(exc)

    semaphore = asyncio.Semaphore(
        min(main_module.COMPLETION_PROMPT_LIST_CONCURRENCY, len(prompts))
    )

    async def _bounded_process(index: int, prompt_text: str):
        async with semaphore:
            return await _process_one_prompt(index, prompt_text)

    processed = await asyncio.gather(
        *[_bounded_process(idx, prompt) for idx, prompt in enumerate(prompts)]
    )
    processed.sort(key=lambda item: item[0])

    completion_choices: List[CompletionChoice] = []
    usage_prompt_tokens = 0
    usage_completion_tokens = 0
    first_request_id = ""

    for idx, prompt, result in processed:
        if idx == 0:
            first_request_id = result.request_id or ""

        output_text = prompt + result.output if request.echo else result.output
        completion_choices.append(
            CompletionChoice(
                index=idx,
                text=output_text,
                finish_reason=main_module._completion_finish_reason(
                    getattr(result, "completion_status", "completed")
                ),
            )
        )

        if result.usage:
            usage_prompt_tokens += result.usage.prompt_tokens
            usage_completion_tokens += result.usage.completion_tokens
        else:
            usage_prompt_tokens += len(prompt.split())
            usage_completion_tokens += len(result.output.split())

    usage = Usage(
        prompt_tokens=usage_prompt_tokens,
        completion_tokens=usage_completion_tokens,
        total_tokens=usage_prompt_tokens + usage_completion_tokens,
    )

    response = CompletionResponse(
        id=request_id or first_request_id or f"cmpl-{int(time.time())}",
        created=int(time.time()),
        model=request.model,
        choices=completion_choices,
        usage=usage,
    )
    partial_reasons = [
        main_module._partial_reason(
            getattr(item[2], "completion_status", "completed"),
            getattr(item[2], "completion_detail", ""),
        )
        for item in processed
    ]
    partial_reasons = [reason for reason in partial_reasons if reason]
    if partial_reasons:
        http_response.headers["X-LarkMemoryCore-Partial-Reason"] = partial_reasons[0]
    return response
