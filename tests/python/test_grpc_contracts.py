# ===- test_grpc_contracts.py -------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Contract tests for the API -> gRPC prompt path and ComputeClient request/response
# mapping. These tests do not require a real model backend.
#
# ===---------------------------------------------------------------------------

import os
import sys
import math
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_test_dir = os.path.abspath(os.path.dirname(__file__))
if _test_dir not in sys.path:
    sys.path.insert(0, _test_dir)

from api_server.auth import ApiKeyAuthManager
import api_server.grpc_client as grpc_client_module
from api_server.grpc_client import ComputeClient, ProcessResult, UsageStats
from api_server.main import app
from openclaw_feishu_cases import (
    OPENCLAW_TIMEZONE_PREFIX_CASES,
    build_real_openclaw_feishu_content_parts,
)


def _disabled_auth_manager() -> ApiKeyAuthManager:
    return ApiKeyAuthManager.from_config(
        legacy_api_key="",
        legacy_key_id="default",
        legacy_scopes="models:read,inference,admin",
        legacy_allowed_models="",
        api_keys_file="",
        api_keys_json="",
    )


def _policy_record(**policy_overrides):
    policy = {
        "api_mode": "both",
        "prompt_style": "buddy_deepseek_r1",
        "default_max_tokens": 32,
        "max_max_tokens": 64,
        "max_input_chars": 2048,
        "request_timeout_ms": 120000,
        "stream_idle_timeout_s": 30,
        "allow_anonymous_models": False,
    }
    policy.update(policy_overrides)
    return {
        "id": "test-model",
        "object": "model",
        "created": 1,
        "owned_by": "ruyi",
        "_serving_policy": policy,
    }


def _wrapped_feishu_body_text(
    body_text: str,
    *,
    message_id: str,
    sender_id: str = "ou_20213a370da42050480fef42e9828099",
    group: bool = False,
    mentioned: bool = False,
    bot_user_id: str = "ou_dd8d6a8d7f5964d8103e7592daf6c27e",
) -> str:
    metadata = {
        "message_id": message_id,
        "sender_id": sender_id,
        "sender": sender_id,
        "timestamp": "Sun 2026-04-12 18:03 UTC",
    }
    if group:
        metadata.update(
            {
                "conversation_label": "oc_group_trace_room",
                "group_subject": "oc_group_trace_room",
                "is_group_chat": True,
            }
        )
    if mentioned:
        metadata["was_mentioned"] = True

    sender_payload = {
        "label": sender_id,
        "id": sender_id,
        "name": sender_id,
    }
    content = (
        "Conversation info (untrusted metadata):\n"
        f"```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n\n"
        "Sender (untrusted metadata):\n"
        f"```json\n{json.dumps(sender_payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"[message_id: {message_id}]\n"
        f"{body_text}"
    )
    if mentioned:
        content += (
            "\n\n"
            "[System: The content may include mention tags in the form "
            "<at user_id=\"...\">name</at>. Treat these as real mentions "
            "of Feishu entities (users or bots).]\n"
            f"[System: If user_id is \"{bot_user_id}\", that mention refers to you.]"
        )
    return content


def _wrapped_feishu_user_text(
    visible_text: str,
    *,
    message_id: str,
    sender_id: str = "ou_20213a370da42050480fef42e9828099",
    group: bool = False,
    mentioned: bool = False,
    bot_user_id: str = "ou_dd8d6a8d7f5964d8103e7592daf6c27e",
) -> str:
    return _wrapped_feishu_body_text(
        f"{sender_id}: {visible_text}",
        message_id=message_id,
        sender_id=sender_id,
        group=group,
        mentioned=mentioned,
        bot_user_id=bot_user_id,
    )


@pytest.mark.asyncio
async def test_chat_completion_formats_buddy_prompt_and_maps_backend_result():
    fake_result = ProcessResult(
        output="backend-ok",
        success=True,
        usage=UsageStats(prompt_tokens=4, completion_tokens=2, latency_ms=15),
        request_id="req-backend",
        completion_status="completed",
    )
    fake_client = SimpleNamespace(process_with_stats=MagicMock(return_value=fake_result))
    routed_backend = SimpleNamespace(
        node_id="node-a",
        public_model_id="test-model",
        backend_model_id="backend-a",
        client=fake_client,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
        "api_server.main._select_backend",
        return_value=routed_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello!"}],
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "req-backend"
    assert body["choices"][0]["message"]["content"] == "backend-ok"
    assert body["usage"]["prompt_tokens"] == 4
    assert body["usage"]["completion_tokens"] == 2
    assert body["usage"]["total_tokens"] == 6

    call_args = fake_client.process_with_stats.call_args.args
    assert call_args[0] == "hello!"
    assert call_args[1] == "backend-a"
    assert call_args[4] == 32


@pytest.mark.asyncio
async def test_chat_completion_maps_multiturn_history_to_latest_user_only():
    fake_result = ProcessResult(
        output="backend-ok",
        success=True,
        usage=UsageStats(prompt_tokens=4, completion_tokens=2, latency_ms=15),
        request_id="req-latest-user",
        completion_status="completed",
    )
    fake_client = SimpleNamespace(process_with_stats=MagicMock(return_value=fake_result))
    routed_backend = SimpleNamespace(
        node_id="node-a",
        public_model_id="test-model",
        backend_model_id="backend-a",
        client=fake_client,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
        "api_server.main._select_backend",
        return_value=routed_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "grpc round-1",
                                message_id="om_grpc_1",
                            ),
                        },
                        {"role": "assistant", "content": "grpc answer-1"},
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "grpc round-2 latest",
                                message_id="om_grpc_2",
                            ),
                        },
                    ],
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "p2p",
                    },
                },
            )

    assert response.status_code == 200
    call_args = fake_client.process_with_stats.call_args.args
    assert call_args[0] == "grpc round-2 latest"
    assert "grpc round-1" not in call_args[0]


@pytest.mark.asyncio
async def test_chat_completion_strips_real_feishu_transport_envelope_before_grpc():
    fake_result = ProcessResult(
        output="backend-ok",
        success=True,
        usage=UsageStats(prompt_tokens=4, completion_tokens=2, latency_ms=15),
        request_id="req-real-envelope-user",
        completion_status="completed",
    )
    fake_client = SimpleNamespace(process_with_stats=MagicMock(return_value=fake_result))
    routed_backend = SimpleNamespace(
        node_id="node-a",
        public_model_id="test-model",
        backend_model_id="backend-a",
        client=fake_client,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
        "api_server.main._select_backend",
        return_value=routed_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": build_real_openclaw_feishu_content_parts(
                                "grpc real envelope round-1",
                                message_id="om_real_grpc_1",
                                transport_timestamp="2026-04-16 09:37:07 GMT+8",
                                message_timestamp="Thu 2026-04-16 09:36 GMT+8",
                            ),
                        },
                        {"role": "assistant", "content": "grpc answer-1"},
                        {
                            "role": "user",
                            "content": build_real_openclaw_feishu_content_parts(
                                "grpc real envelope round-2 latest",
                                message_id="om_real_grpc_2",
                                transport_timestamp="2026-04-16 10:35:31 GMT+8",
                                message_timestamp="Thu 2026-04-16 10:30 GMT+8",
                            ),
                        },
                    ],
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "p2p",
                    },
                },
            )

    assert response.status_code == 200
    prompt = fake_client.process_with_stats.call_args.args[0]
    assert prompt == "grpc real envelope round-2 latest"
    assert "Conversation info (untrusted metadata)" not in prompt
    assert "Feishu[default] DM" not in prompt


@pytest.mark.asyncio
async def test_chat_completion_chatml_sends_cleaned_latest_user_only_to_grpc():
    fake_result = ProcessResult(
        output="backend-ok",
        success=True,
        usage=UsageStats(prompt_tokens=4, completion_tokens=2, latency_ms=15),
        request_id="req-chatml-cleaned-user",
        completion_status="completed",
    )
    fake_client = SimpleNamespace(process_with_stats=MagicMock(return_value=fake_result))
    routed_backend = SimpleNamespace(
        node_id="node-a",
        public_model_id="test-model",
        backend_model_id="backend-a",
        client=fake_client,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch(
        "api_server.main._get_model_record",
        return_value=_policy_record(prompt_style="chatml"),
    ), patch(
        "api_server.main._select_backend",
        return_value=routed_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "@Ruyi Test Bot chatml round-1",
                                message_id="om_chatml_1",
                                group=True,
                                mentioned=True,
                            ),
                        },
                        {"role": "assistant", "content": "chatml answer-1"},
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "Test Bot chatml round-2 latest",
                                message_id="om_chatml_2",
                                group=True,
                                mentioned=True,
                            ),
                        },
                    ],
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "group",
                    },
                },
            )

    assert response.status_code == 200
    prompt = fake_client.process_with_stats.call_args.args[0]
    assert "chatml round-2 latest" in prompt
    assert "Test Bot chatml round-2 latest" not in prompt
    assert "Conversation info (untrusted metadata)" not in prompt
    assert "Sender (untrusted metadata)" not in prompt
    assert "chatml round-1" not in prompt


@pytest.mark.asyncio
async def test_plain_openai_text_is_not_aggressively_rewritten_before_grpc():
    fake_result = ProcessResult(
        output="backend-ok",
        success=True,
        usage=UsageStats(prompt_tokens=4, completion_tokens=2, latency_ms=15),
        request_id="req-plain-openai-user",
        completion_status="completed",
    )
    fake_client = SimpleNamespace(process_with_stats=MagicMock(return_value=fake_result))
    routed_backend = SimpleNamespace(
        node_id="node-a",
        public_model_id="test-model",
        backend_model_id="backend-a",
        client=fake_client,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
        "api_server.main._select_backend",
        return_value=routed_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Test Bot summarize this exactly as written",
                        }
                    ],
                },
            )

    assert response.status_code == 200
    prompt = fake_client.process_with_stats.call_args.args[0]
    assert prompt == "Test Bot summarize this exactly as written"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped_user_content", OPENCLAW_TIMEZONE_PREFIX_CASES)
async def test_chat_completion_strips_leading_timezone_prefix_before_grpc(
    wrapped_user_content,
):
    fake_result = ProcessResult(
        output="backend-ok",
        success=True,
        usage=UsageStats(prompt_tokens=4, completion_tokens=2, latency_ms=15),
        request_id="req-time-prefix-cleaned-user",
        completion_status="completed",
    )
    fake_client = SimpleNamespace(process_with_stats=MagicMock(return_value=fake_result))
    routed_backend = SimpleNamespace(
        node_id="node-a",
        public_model_id="test-model",
        backend_model_id="backend-a",
        client=fake_client,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
        "api_server.main._select_backend",
        return_value=routed_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": _wrapped_feishu_body_text(
                                wrapped_user_content,
                                message_id="om_time_grpc_1",
                            ),
                        }
                    ],
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "p2p",
                    },
                },
            )

    assert response.status_code == 200
    prompt = fake_client.process_with_stats.call_args.args[0]
    assert prompt == "nice to meet you"
    assert "GMT+8" not in prompt


@pytest.mark.asyncio
async def test_streaming_chat_completion_sends_latest_user_only_to_grpc():
    fake_client = SimpleNamespace()

    def _stream_events():
        yield SimpleNamespace(
            content="OK",
            is_final=False,
            error_message="",
            completion_status="completed",
            completion_detail="",
        )
        yield SimpleNamespace(
            content="",
            is_final=True,
            error_message="",
            completion_status="completed",
            completion_detail="",
        )

    fake_client.process_stream = MagicMock(return_value=_stream_events())
    routed_backend = SimpleNamespace(
        node_id="node-a",
        public_model_id="test-model",
        backend_model_id="backend-a",
        client=fake_client,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
        "api_server.main._select_backend",
        return_value=routed_backend,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "@Ruyi Test Bot stream grpc round-1",
                                message_id="om_stream_grpc_1",
                                group=True,
                                mentioned=True,
                            ),
                        },
                        {"role": "assistant", "content": "stream grpc answer-1"},
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "Test Bot stream grpc round-2 latest",
                                message_id="om_stream_grpc_2",
                                group=True,
                                mentioned=True,
                            ),
                        },
                    ],
                    "stream": True,
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "group",
                    },
                },
            ) as response:
                body = await response.aread()

    assert response.status_code == 200
    assert b"data: [DONE]" in body
    call_args = fake_client.process_stream.call_args.args
    assert call_args[0] == "stream grpc round-2 latest"
    assert "stream grpc round-1" not in call_args[0]


def test_compute_client_process_with_stats_builds_process_request_and_maps_usage():
    client = ComputeClient.__new__(ComputeClient)
    client.stub = MagicMock()
    client._ensure_connected = MagicMock()
    client._execute_with_retry = lambda fn, timeout: fn()

    response = grpc_client_module.compute_pb2.ProcessResponse(
        output="backend-ok",
        success=True,
        request_id="req-123",
        completion_status="completed",
        completion_detail="",
    )
    response.usage.prompt_tokens = 4
    response.usage.completion_tokens = 7
    response.usage.latency_ms = 99
    response.usage.tokens_per_second = 3.5
    client.stub.Process.return_value = response

    result = client.process_with_stats(
        "User: hello!",
        model_id="model-a",
        timeout=12.0,
        temperature=0.0,
        max_tokens=16,
        top_p=0.9,
        top_k=40,
        repetition_penalty=1.1,
        seed=7,
        request_id="req-123",
        request_timeout_ms=9000,
    )

    sent_request = client.stub.Process.call_args.args[0]
    sent_timeout = client.stub.Process.call_args.kwargs["timeout"]

    assert sent_request.input == "User: hello!"
    assert sent_request.model_id == "model-a"
    assert sent_request.temperature == 0.0
    assert sent_request.max_tokens == 16
    assert math.isclose(sent_request.top_p, 0.9, rel_tol=1e-6)
    assert sent_request.top_k == 40
    assert math.isclose(sent_request.repetition_penalty, 1.1, rel_tol=1e-6)
    assert sent_request.seed == 7
    assert sent_request.request_id == "req-123"
    assert sent_request.timeout_ms == 9000
    assert sent_timeout == 12.0

    assert result.output == "backend-ok"
    assert result.success is True
    assert result.request_id == "req-123"
    assert result.completion_status == "completed"
    assert result.usage is not None
    assert result.usage.prompt_tokens == 4
    assert result.usage.completion_tokens == 7
    assert result.usage.latency_ms == 99
    assert math.isclose(result.usage.tokens_per_second, 3.5, rel_tol=1e-6)
