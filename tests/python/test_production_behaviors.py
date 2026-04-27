# ===- test_production_behaviors.py -------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Tests for production hardening response behaviors.
#
# ===---------------------------------------------------------------------------

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import grpc
import pytest
from httpx import ASGITransport, AsyncClient

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_test_dir = os.path.abspath(os.path.dirname(__file__))
if _test_dir not in sys.path:
    sys.path.insert(0, _test_dir)

from api_server.auth import ApiKeyAuthManager
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
        "stream_idle_timeout_s": 15,
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


class _FakeRpcError(grpc.RpcError):
    def __init__(self, status_code: grpc.StatusCode, details: str):
        self._status_code = status_code
        self._details = details

    def code(self):
        return self._status_code

    def details(self):
        return self._details


def _completed_result(output: str, request_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        output=output,
        request_id=request_id,
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
        completion_status="completed",
        completion_detail="",
    )


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
async def test_chat_policy_defaults_max_tokens_and_returns_partial_header():
    fake_result = SimpleNamespace(
        output="partial answer",
        request_id="req-1",
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
        completion_status="partial_timeout",
        completion_detail="watchdog_timeout",
    )
    policy_record = _policy_record(default_max_tokens=48)

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats", return_value=fake_result
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

    assert response.status_code == 200
    assert response.headers["X-Ruyi-Partial-Reason"] == "watchdog_timeout"
    assert response.json()["choices"][0]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_completion_policy_rejects_endpoint_mismatch():
    policy_record = _policy_record(api_mode="chat")
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/completions",
                json={"model": "test-model", "prompt": "hello"},
            )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_model_policy_rejects_excessive_max_tokens():
    policy_record = _policy_record(max_max_tokens=8)
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 32,
                },
            )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_overload_maps_to_503():
    policy_record = _policy_record()
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_FakeRpcError(
            grpc.StatusCode.RESOURCE_EXHAUSTED, "Server overloaded"
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_cancelled_request_maps_to_409():
    policy_record = _policy_record()
    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_FakeRpcError(grpc.StatusCode.CANCELLED, "Request cancelled"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_anonymous_models_visible_when_policy_allows_it():
    manager = ApiKeyAuthManager.from_config(
        legacy_api_key="",
        legacy_key_id="default",
        legacy_scopes="models:read,inference,admin",
        legacy_allowed_models="",
        api_keys_file="",
        api_keys_json='{"keys":[{"key_id":"tenant-a","secret":"sk-tenant-a"}]}',
    )
    fake_models = [_policy_record(allow_anonymous_models=True)]
    with patch("api_server.main.auth_manager", manager), patch(
        "api_server.main._load_models_from_compute",
        return_value=fake_models,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "test-model"


@pytest.mark.asyncio
async def test_feishu_dm_two_turn_chat_accepts_extra_fields_without_422():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("READY", "req-openclaw-single")

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_process_with_stats,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {"role": "user", "content": "dm trace token round-1"},
                        {"role": "assistant", "content": "round-1 answer"},
                        {"role": "user", "content": "dm trace token round-2 latest"},
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "p2p",
                        "conversation_id": "oc-feishu-dm-001",
                    },
                    "openclaw": {
                        "channel": "feishu",
                        "entrypoint": "dm",
                    },
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "list_models",
                                "description": "List configured models",
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                },
                            },
                        }
                    ],
                    "tool_choice": "auto",
                    "response_format": {"type": "json_object"},
                },
            )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "READY"
    assert len(captured_prompts) == 1
    assert captured_prompts[0].strip() == "dm trace token round-2 latest"


@pytest.mark.asyncio
async def test_chat_debug_trace_logs_prompt_and_result_when_enabled():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )

    with patch.dict(os.environ, {"RUYI_DEBUG_PROMPT_IO": "1"}, clear=False), patch(
        "api_server.main.auth_manager", _disabled_auth_manager()
    ), patch("api_server.main._ensure_model_available"), patch(
        "api_server.main._get_model_record", return_value=policy_record
    ), patch(
        "api_server.main.compute_client.process_with_stats",
        return_value=_completed_result("READY", "req-debug-trace"),
    ), patch("api_server.main.logger.info") as mock_logger_info:
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
                                "hello trace round-1",
                                message_id="om_trace_1",
                            ),
                        },
                        {"role": "assistant", "content": "trace answer round-1"},
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "hello trace round-2 latest",
                                message_id="om_trace_2",
                            ),
                        },
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "p2p",
                        "conversation_id": "oc-feishu-dm-trace",
                    },
                },
            )

    assert response.status_code == 200

    trace_calls = {
        call.args[0]: call.kwargs["extra"]
        for call in mock_logger_info.call_args_list
        if call.args
        and call.args[0]
        in {
            "API server received raw request",
            "API server received prompt",
            "API server returning result",
        }
    }
    assert "API server received raw request" in trace_calls
    assert "API server received prompt" in trace_calls
    assert "API server returning result" in trace_calls
    assert "hello trace round-1" in trace_calls["API server received raw request"]["raw_request"]
    assert "hello trace round-2 latest" in trace_calls["API server received raw request"]["raw_request"]
    assert "Conversation info (untrusted metadata)" in trace_calls["API server received raw request"]["raw_request"]
    assert trace_calls["API server received prompt"]["prompt"] == "hello trace round-2 latest"
    assert "hello trace round-1" not in trace_calls["API server received prompt"]["prompt"]
    assert "Conversation info (untrusted metadata)" not in trace_calls["API server received prompt"]["prompt"]
    assert trace_calls["API server received raw request"]["request_id"] == response.headers["X-Request-Id"]
    assert trace_calls["API server received prompt"]["prompt_chars"] == len(
        trace_calls["API server received prompt"]["prompt"]
    )
    assert trace_calls["API server returning result"]["result"] == "READY"
    assert trace_calls["API server returning result"]["result_chars"] == 5
    assert trace_calls["API server returning result"]["completion_status"] == "completed"


@pytest.mark.asyncio
async def test_chat_debug_trace_cleans_real_feishu_transport_envelope_from_content_parts():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )

    with patch.dict(os.environ, {"RUYI_DEBUG_PROMPT_IO": "1"}, clear=False), patch(
        "api_server.main.auth_manager", _disabled_auth_manager()
    ), patch("api_server.main._ensure_model_available"), patch(
        "api_server.main._get_model_record", return_value=policy_record
    ), patch(
        "api_server.main.compute_client.process_with_stats",
        return_value=_completed_result("READY", "req-real-trace"),
    ), patch("api_server.main.logger.info") as mock_logger_info:
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
                                "real envelope round-1",
                                message_id="om_real_trace_1",
                                transport_timestamp="2026-04-16 09:37:07 GMT+8",
                                message_timestamp="Thu 2026-04-16 09:36 GMT+8",
                            ),
                        },
                        {"role": "assistant", "content": "trace answer round-1"},
                        {
                            "role": "user",
                            "content": build_real_openclaw_feishu_content_parts(
                                "real envelope round-2 latest",
                                message_id="om_real_trace_2",
                                transport_timestamp="2026-04-16 10:35:31 GMT+8",
                                message_timestamp="Thu 2026-04-16 10:30 GMT+8",
                            ),
                        },
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "p2p",
                        "conversation_id": "oc-feishu-dm-real-trace",
                    },
                },
            )

    assert response.status_code == 200
    trace_calls = {
        call.args[0]: call.kwargs["extra"]
        for call in mock_logger_info.call_args_list
        if call.args
        and call.args[0]
        in {
            "API server received raw request",
            "API server received prompt",
            "API server returning result",
        }
    }
    assert "System: [2026-04-16 10:35:31 GMT+8] Feishu[default] DM" in trace_calls[
        "API server received raw request"
    ]["raw_request"]
    assert (
        trace_calls["API server received prompt"]["prompt"]
        == "real envelope round-2 latest"
    )
    assert "Conversation info (untrusted metadata)" not in trace_calls[
        "API server received prompt"
    ]["prompt"]
    assert "System: [2026-04-16" not in trace_calls["API server received prompt"][
        "prompt"
    ]


@pytest.mark.asyncio
async def test_feishu_group_at_bot_two_turn_chat_uses_latest_user_only():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("Final answer", "req-openclaw-multiturn")

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_process_with_stats,
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
                                "@Ruyi Test Bot group round-1",
                                message_id="om_group_1",
                                group=True,
                                mentioned=True,
                            ),
                        },
                        {"role": "assistant", "content": "group round-1 answer"},
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "Ruyi Test Bot group round-2 latest",
                                message_id="om_group_2",
                                group=True,
                                mentioned=True,
                            ),
                        },
                    ],
                    "max_tokens": 32,
                    "temperature": 0.0,
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "group",
                        "conversation_id": "oc-feishu-group-001",
                        "mentions": ["ruyi-bot"],
                    },
                    "openclaw": {
                        "channel": "feishu",
                        "entrypoint": "group_at_bot",
                    },
                },
            )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Final answer"
    assert len(captured_prompts) == 1
    assert captured_prompts[0].strip() == "group round-2 latest"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped_user_content", OPENCLAW_TIMEZONE_PREFIX_CASES)
async def test_feishu_wrapper_cleanup_strips_leading_timezone_prefix(
    wrapped_user_content,
):
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("READY", "req-feishu-time-prefix")

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_process_with_stats,
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
                                message_id="om_time_prefix_1",
                            ),
                        }
                    ],
                    "max_tokens": 32,
                    "temperature": 0.0,
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "p2p",
                    },
                },
            )

    assert response.status_code == 200
    assert len(captured_prompts) == 1
    assert captured_prompts[0].strip() == "nice to meet you"


@pytest.mark.asyncio
async def test_feishu_stream_request_returns_sse_and_uses_latest_user_prompt():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_stream(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        yield SimpleNamespace(
            content="READY",
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

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_stream",
        side_effect=_process_stream,
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
                                "@Ruyi Test Bot stream round-1",
                                message_id="om_stream_1",
                                group=True,
                                mentioned=True,
                            ),
                        },
                        {"role": "assistant", "content": "stream answer round-1"},
                        {
                            "role": "user",
                            "content": _wrapped_feishu_user_text(
                                "Test Bot stream round-2 latest",
                                message_id="om_stream_2",
                                group=True,
                                mentioned=True,
                            ),
                        },
                    ],
                    "stream": True,
                    "max_tokens": 16,
                    "temperature": 0.0,
                    "metadata": {
                        "source": "openclaw-feishu",
                        "chat_type": "group",
                        "conversation_id": "oc-feishu-stream-001",
                    },
                },
            ) as response:
                body_lines = [line async for line in response.aiter_lines() if line]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-Request-Id"]
    assert len(captured_prompts) == 1
    assert captured_prompts[0].strip() == "stream round-2 latest"
    assert body_lines[-1] == "data: [DONE]"
    first_payload = json.loads(body_lines[0][len("data: "):])
    finish_payload = json.loads(body_lines[-2][len("data: "):])
    assert first_payload["choices"][0]["delta"]["content"] == "READY"
    assert finish_payload["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_openwebui_text_content_parts_are_normalized_into_prompt():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("READY", "req-openwebui-content-parts")

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_process_with_stats,
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
                            "content": [
                                {"type": "text", "text": "Hello"},
                                {"type": "text", "text": " from WebUI"},
                            ],
                        }
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                },
            )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "READY"
    assert len(captured_prompts) == 1
    assert captured_prompts[0] == "Hello from WebUI"


@pytest.mark.asyncio
async def test_openwebui_non_text_content_parts_return_400():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": "https://example.com/a.png"},
                                }
                            ],
                        }
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                },
            )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "Only text content parts are supported" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_openwebui_trailing_empty_assistant_placeholder_is_ignored():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("READY", "req-openwebui-empty-assistant")

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_process_with_stats,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": ""},
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                },
            )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "READY"
    assert len(captured_prompts) == 1
    assert captured_prompts[0].strip() == "hello"


@pytest.mark.asyncio
async def test_openwebui_tool_history_accepts_null_assistant_content():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("Final answer", "req-openwebui-tool-history")

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_process_with_stats,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_weather",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": "{\"city\":\"Shanghai\"}",
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call_weather",
                            "content": "{\"temperature_c\":21,\"condition\":\"clear\"}",
                        },
                        {
                            "role": "user",
                            "content": "Summarize the weather.",
                        },
                    ],
                    "max_tokens": 32,
                    "temperature": 0.0,
                },
            )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Final answer"
    assert len(captured_prompts) == 1
    assert captured_prompts[0].strip() == "Summarize the weather."


@pytest.mark.asyncio
async def test_feishu_wrapper_cleanup_falls_back_when_cleaned_question_is_empty():
    policy_record = _policy_record(
        prompt_style="buddy_deepseek_r1",
        max_input_chars=8192,
    )
    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("READY", "req-feishu-empty-fallback")

    with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
        "api_server.main._ensure_model_available"
    ), patch("api_server.main._get_model_record", return_value=policy_record), patch(
        "api_server.main.compute_client.process_with_stats",
        side_effect=_process_with_stats,
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
                                "@Ruyi Test Bot",
                                message_id="om_empty_1",
                                group=True,
                                mentioned=True,
                            ),
                        }
                    ],
                    "max_tokens": 16,
                    "temperature": 0.0,
                },
            )

    assert response.status_code == 200
    assert len(captured_prompts) == 1
    assert captured_prompts[0].strip() != "User:"
    assert "Conversation info (untrusted metadata)" in captured_prompts[0]
