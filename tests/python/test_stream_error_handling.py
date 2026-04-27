# ===- test_stream_error_handling.py -------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Unit tests for the enhanced streaming response error handling in
# api_server/main.py — specifically the _stream_chat_response and
# _stream_completion_response generators.
#
# Requirements: 6.2, 6.4
#
# ===---------------------------------------------------------------------------

import os
import sys
import json
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import grpc

# Ensure the project root is on sys.path so that api_server can be imported.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_test_dir = os.path.abspath(os.path.dirname(__file__))
if _test_dir not in sys.path:
    sys.path.insert(0, _test_dir)

from api_server.main import (
    _stream_chat_response,
    _stream_completion_response,
    ChatCompletionRequest,
    CompletionRequest,
    Message,
    app,
)
from api_server.auth import ApiKeyAuthManager
from openclaw_feishu_cases import (
    OPENCLAW_TIMEZONE_PREFIX_CASES,
    build_real_openclaw_feishu_content_parts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chat_request(model: str = "test-model") -> ChatCompletionRequest:
    """Create a minimal ChatCompletionRequest for testing."""
    return ChatCompletionRequest(
        model=model,
        messages=[Message(role="user", content="hello")],
        stream=True,
    )


def _make_completion_request(model: str = "test-model") -> CompletionRequest:
    """Create a minimal CompletionRequest for testing."""
    return CompletionRequest(
        model=model,
        prompt="hello",
        stream=True,
    )


def _make_http_request(disconnected: bool = False) -> MagicMock:
    """Create a mock Starlette Request with is_disconnected() support."""
    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=disconnected)
    return mock_request


class _FakeGrpcError(grpc.RpcError):
    """A fake gRPC error for testing that implements the grpc.RpcError interface."""

    def __init__(self, code: grpc.StatusCode, details: str):
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details

    def __str__(self):
        return f"<_FakeGrpcError: {self._code.name} {self._details}>"


async def _collect_stream(async_gen):
    """Collect all items from an async generator into a list."""
    items = []
    async for item in async_gen:
        items.append(item)
    return items


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


# ---------------------------------------------------------------------------
# Tests: _stream_chat_response — gRPC error handling (Req 6.2)
# ---------------------------------------------------------------------------


class TestStreamChatResponseGrpcError:
    """Test that _stream_chat_response catches grpc.RpcError and sends SSE error events."""

    @pytest.mark.asyncio
    async def test_grpc_unavailable_sends_sse_error(self):
        """When gRPC stream raises UNAVAILABLE, an SSE error event with
        'Stream interrupted' message and 'server_error' type should be yielded."""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)

        grpc_error = _FakeGrpcError(
            grpc.StatusCode.UNAVAILABLE, "Connection refused"
        )

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = grpc_error

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        # Should have exactly one SSE error event
        assert len(chunks) == 1
        assert chunks[0].startswith("data: ")
        payload = json.loads(chunks[0][len("data: "):].strip())
        assert "error" in payload
        assert "Stream interrupted" in payload["error"]["message"]
        assert "Connection refused" in payload["error"]["message"]
        assert payload["error"]["type"] == "server_error"

    @pytest.mark.asyncio
    async def test_grpc_deadline_exceeded_sends_sse_error(self):
        """When gRPC stream raises DEADLINE_EXCEEDED, an SSE error event should be yielded."""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)

        grpc_error = _FakeGrpcError(
            grpc.StatusCode.DEADLINE_EXCEEDED, "Deadline exceeded"
        )

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = grpc_error

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        assert len(chunks) == 1
        payload = json.loads(chunks[0][len("data: "):].strip())
        assert "Stream interrupted" in payload["error"]["message"]
        assert "Deadline exceeded" in payload["error"]["message"]
        assert payload["error"]["type"] == "server_error"

    @pytest.mark.asyncio
    async def test_grpc_error_mid_stream_sends_sse_error(self):
        """When gRPC error occurs after some chunks have been yielded,
        the error event should follow the successful chunks."""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)

        grpc_error = _FakeGrpcError(
            grpc.StatusCode.UNAVAILABLE, "Stream broken"
        )

        def _stream_then_fail(*args, **kwargs):
            """Generator that yields one chunk then raises a gRPC error."""
            yield "Hello"
            raise grpc_error

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = _stream_then_fail

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        # First chunk is the successful content, second is the error
        assert len(chunks) == 2
        # First chunk should be a valid SSE data chunk
        first_payload = json.loads(chunks[0][len("data: "):].strip())
        assert first_payload["object"] == "chat.completion.chunk"
        assert first_payload["choices"][0]["delta"]["content"] == "Hello"

        # Second chunk should be the error
        error_payload = json.loads(chunks[1][len("data: "):].strip())
        assert "Stream interrupted" in error_payload["error"]["message"]
        assert error_payload["error"]["type"] == "server_error"

    @pytest.mark.asyncio
    async def test_general_exception_still_caught(self):
        """Non-gRPC exceptions should still be caught by the general Exception handler."""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = RuntimeError("Something went wrong")

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        assert len(chunks) == 1
        payload = json.loads(chunks[0][len("data: "):].strip())
        assert "Something went wrong" in payload["error"]["message"]
        assert payload["error"]["type"] == "server_error"


# ---------------------------------------------------------------------------
# Tests: _stream_chat_response — client disconnect detection (Req 6.4)
# ---------------------------------------------------------------------------


class TestStreamChatResponseClientDisconnect:
    """Test that _stream_chat_response detects client disconnection and cancels the gRPC stream."""

    @pytest.mark.asyncio
    async def test_disconnect_stops_streaming(self):
        """When client disconnects after first chunk, streaming should stop
        and no finish/DONE events should be yielded."""
        chat_req = _make_chat_request()

        # Simulate: connected for first chunk, disconnected for second
        http_req = MagicMock()
        http_req.is_disconnected = AsyncMock(side_effect=[False, True])

        def _multi_chunk_stream(*args, **kwargs):
            yield "chunk1"
            yield "chunk2"
            yield "chunk3"

        with patch("api_server.main.compute_client") as mock_client:
            mock_gen = _multi_chunk_stream()
            mock_client.process_stream.return_value = mock_gen

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        # Should have yielded only the first two chunks (chunk1 ok, chunk2 triggers disconnect check)
        # chunk1: yield + is_disconnected returns False -> continue
        # chunk2: yield + is_disconnected returns True -> close and return
        assert len(chunks) == 2
        # Both should be valid SSE data chunks, no [DONE] or finish_reason
        for chunk in chunks:
            assert chunk.startswith("data: ")
            payload = json.loads(chunk[len("data: "):].strip())
            assert "error" not in payload

    @pytest.mark.asyncio
    async def test_stream_debug_trace_logs_aggregated_result(self):
        """When prompt tracing is enabled, the API server should log the
        concatenated stream result once the stream completes."""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)
        http_req.state.request_id = "req-stream-trace"
        routed_backend = SimpleNamespace(
            node_id="node-a",
            public_model_id="test-model",
            backend_model_id="backend-a",
            client=SimpleNamespace(),
        )

        def _stream_events():
            yield SimpleNamespace(
                content="Hel",
                is_final=False,
                error_message="",
                completion_status="completed",
                completion_detail="",
            )
            yield SimpleNamespace(
                content="lo",
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

        routed_backend.client.process_stream = MagicMock(return_value=_stream_events())

        with patch.dict(os.environ, {"RUYI_DEBUG_PROMPT_IO": "1"}, clear=False), patch(
            "api_server.main._get_model_record", return_value=_policy_record()
        ), patch("api_server.main.logger.info") as mock_logger_info:
            chunks = await _collect_stream(
                _stream_chat_response(
                    chat_req,
                    "User: hello",
                    http_req,
                    routed_backend,
                )
            )

        assert chunks[-1] == "data: [DONE]\n\n"
        matching_calls = [
            call.kwargs["extra"]
            for call in mock_logger_info.call_args_list
            if call.args and call.args[0] == "API server returning streaming result"
        ]
        assert matching_calls
        assert matching_calls[-1]["request_id"] == "req-stream-trace"
        assert matching_calls[-1]["result"] == "Hello"
        assert matching_calls[-1]["result_chars"] == 5
        assert matching_calls[-1]["completion_status"] == "completed"

    @pytest.mark.asyncio
    async def test_no_disconnect_completes_normally(self):
        """When client stays connected, streaming should complete with finish and DONE."""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)

        def _single_chunk_stream(*args, **kwargs):
            yield "hello"

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.return_value = _single_chunk_stream()

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        # Should have: content chunk + finish chunk + [DONE]
        assert len(chunks) == 3
        # Last chunk should be [DONE]
        assert chunks[-1] == "data: [DONE]\n\n"
        # Second-to-last should have finish_reason="stop"
        finish_payload = json.loads(chunks[-2][len("data: "):].strip())
        assert finish_payload["choices"][0]["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# Tests: _stream_completion_response — gRPC error handling (Req 6.2)
# ---------------------------------------------------------------------------


class TestStreamCompletionResponseGrpcError:
    """Test that _stream_completion_response catches grpc.RpcError and sends SSE error events."""

    @pytest.mark.asyncio
    async def test_grpc_unavailable_sends_sse_error(self):
        """When gRPC stream raises UNAVAILABLE, an SSE error event should be yielded."""
        comp_req = _make_completion_request()
        http_req = _make_http_request(disconnected=False)

        grpc_error = _FakeGrpcError(
            grpc.StatusCode.UNAVAILABLE, "Connection lost"
        )

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = grpc_error

            chunks = await _collect_stream(
                _stream_completion_response(comp_req, "test prompt", http_req)
            )

        assert len(chunks) == 1
        payload = json.loads(chunks[0][len("data: "):].strip())
        assert "Stream interrupted" in payload["error"]["message"]
        assert "Connection lost" in payload["error"]["message"]
        assert payload["error"]["type"] == "server_error"

    @pytest.mark.asyncio
    async def test_grpc_internal_error_sends_sse_error(self):
        """When gRPC stream raises INTERNAL, an SSE error event should be yielded."""
        comp_req = _make_completion_request()
        http_req = _make_http_request(disconnected=False)

        grpc_error = _FakeGrpcError(
            grpc.StatusCode.INTERNAL, "Internal server error"
        )

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = grpc_error

            chunks = await _collect_stream(
                _stream_completion_response(comp_req, "test prompt", http_req)
            )

        assert len(chunks) == 1
        payload = json.loads(chunks[0][len("data: "):].strip())
        assert "Stream interrupted" in payload["error"]["message"]
        assert payload["error"]["type"] == "server_error"

    @pytest.mark.asyncio
    async def test_grpc_error_mid_stream_sends_sse_error(self):
        """When gRPC error occurs mid-stream, error event follows successful chunks."""
        comp_req = _make_completion_request()
        http_req = _make_http_request(disconnected=False)

        grpc_error = _FakeGrpcError(
            grpc.StatusCode.UNAVAILABLE, "Network failure"
        )

        def _stream_then_fail(*args, **kwargs):
            yield "partial"
            raise grpc_error

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = _stream_then_fail

            chunks = await _collect_stream(
                _stream_completion_response(comp_req, "test prompt", http_req)
            )

        assert len(chunks) == 2
        # First chunk is successful content
        first_payload = json.loads(chunks[0][len("data: "):].strip())
        assert first_payload["object"] == "text_completion"
        assert first_payload["choices"][0]["text"] == "partial"

        # Second chunk is the error
        error_payload = json.loads(chunks[1][len("data: "):].strip())
        assert "Stream interrupted" in error_payload["error"]["message"]

    @pytest.mark.asyncio
    async def test_general_exception_still_caught(self):
        """Non-gRPC exceptions should still be caught by the general Exception handler."""
        comp_req = _make_completion_request()
        http_req = _make_http_request(disconnected=False)

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = ValueError("Bad value")

            chunks = await _collect_stream(
                _stream_completion_response(comp_req, "test prompt", http_req)
            )

        assert len(chunks) == 1
        payload = json.loads(chunks[0][len("data: "):].strip())
        assert "Bad value" in payload["error"]["message"]
        assert payload["error"]["type"] == "server_error"


# ---------------------------------------------------------------------------
# Tests: _stream_completion_response — client disconnect detection (Req 6.4)
# ---------------------------------------------------------------------------


class TestStreamCompletionResponseClientDisconnect:
    """Test that _stream_completion_response detects client disconnection."""

    @pytest.mark.asyncio
    async def test_disconnect_stops_streaming(self):
        """When client disconnects, streaming should stop and gRPC stream should be closed."""
        comp_req = _make_completion_request()

        http_req = MagicMock()
        http_req.is_disconnected = AsyncMock(side_effect=[False, True])

        def _multi_chunk_stream(*args, **kwargs):
            yield "chunk1"
            yield "chunk2"
            yield "chunk3"

        with patch("api_server.main.compute_client") as mock_client:
            mock_gen = _multi_chunk_stream()
            mock_client.process_stream.return_value = mock_gen

            chunks = await _collect_stream(
                _stream_completion_response(comp_req, "test prompt", http_req)
            )

        # Should stop after detecting disconnect
        assert len(chunks) == 2
        for chunk in chunks:
            assert chunk.startswith("data: ")
            payload = json.loads(chunk[len("data: "):].strip())
            assert "error" not in payload

    @pytest.mark.asyncio
    async def test_no_disconnect_completes_normally(self):
        """When client stays connected, streaming should complete with finish and DONE."""
        comp_req = _make_completion_request()
        http_req = _make_http_request(disconnected=False)

        def _single_chunk_stream(*args, **kwargs):
            yield "world"

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.return_value = _single_chunk_stream()

            chunks = await _collect_stream(
                _stream_completion_response(comp_req, "test prompt", http_req)
            )

        assert len(chunks) == 3
        assert chunks[-1] == "data: [DONE]\n\n"
        finish_payload = json.loads(chunks[-2][len("data: "):].strip())
        assert finish_payload["choices"][0]["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# Tests: SSE error event format validation (Req 6.2)
# ---------------------------------------------------------------------------


class TestSSEErrorEventFormat:
    """Validate the exact format of SSE error events matches the design spec."""

    @pytest.mark.asyncio
    async def test_sse_error_format_matches_spec(self):
        """SSE error event should match: data: {"error": {"message": "Stream interrupted: ...", "type": "server_error"}}"""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)

        grpc_error = _FakeGrpcError(
            grpc.StatusCode.UNAVAILABLE, "server down"
        )

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = grpc_error

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        assert len(chunks) == 1
        raw = chunks[0]
        # Must start with "data: " and end with "\n\n"
        assert raw.startswith("data: ")
        assert raw.endswith("\n\n")

        # Parse the JSON payload
        json_str = raw[len("data: "):-len("\n\n")]
        payload = json.loads(json_str)

        # Validate structure
        assert set(payload.keys()) == {"error"}
        assert {"message", "type"}.issubset(set(payload["error"].keys()))
        assert payload["error"]["type"] == "server_error"
        assert payload["error"]["message"].startswith("Stream interrupted: ")

    @pytest.mark.asyncio
    async def test_sse_error_contains_grpc_details(self):
        """The SSE error message should contain the gRPC error details."""
        chat_req = _make_chat_request()
        http_req = _make_http_request(disconnected=False)

        specific_details = "upstream compute node crashed unexpectedly"
        grpc_error = _FakeGrpcError(
            grpc.StatusCode.INTERNAL, specific_details
        )

        with patch("api_server.main.compute_client") as mock_client:
            mock_client.process_stream.side_effect = grpc_error

            chunks = await _collect_stream(
                _stream_chat_response(chat_req, "test prompt", http_req)
            )

        payload = json.loads(chunks[0][len("data: "):].strip())
        assert specific_details in payload["error"]["message"]


# ---------------------------------------------------------------------------
# Tests: Feishu/OpenClaw SSE endpoint behavior
# ---------------------------------------------------------------------------


class TestFeishuStreamingEndpointBehavior:
    """Exercise the public SSE endpoint with Feishu-style multi-turn payloads."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("wrapped_user_content", OPENCLAW_TIMEZONE_PREFIX_CASES)
    async def test_feishu_stream_endpoint_strips_leading_timezone_prefix(
        self, wrapped_user_content
    ):
        captured_prompts = []

        def _stream_events(*args, **kwargs):
            captured_prompts.append(args[0])
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
        ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
            "api_server.main.compute_client.process_stream",
            side_effect=_stream_events,
        ):
            from httpx import AsyncClient, ASGITransport

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
                                "content": _wrapped_feishu_body_text(
                                    wrapped_user_content,
                                    message_id="om_sse_time_1",
                                ),
                            }
                        ],
                        "stream": True,
                        "metadata": {
                            "source": "openclaw-feishu",
                            "chat_type": "p2p",
                            "conversation_id": "oc-feishu-sse-dm-time-001",
                        },
                    },
                ) as response:
                    lines = [line async for line in response.aiter_lines() if line]

        assert response.status_code == 200
        assert captured_prompts == ["nice to meet you"]
        assert lines[-1] == "data: [DONE]"

    @pytest.mark.asyncio
    async def test_feishu_stream_endpoint_preserves_sse_shape(self):
        captured_prompts = []

        def _stream_events(*args, **kwargs):
            captured_prompts.append(args[0])
            yield SimpleNamespace(
                content="Fei",
                is_final=False,
                error_message="",
                completion_status="completed",
                completion_detail="",
            )
            yield SimpleNamespace(
                content="shu",
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
        ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
            "api_server.main.compute_client.process_stream",
            side_effect=_stream_events,
        ):
            from httpx import AsyncClient, ASGITransport

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
                                    "@Ruyi Test Bot dm sse round-1",
                                    message_id="om_sse_1",
                                    group=True,
                                    mentioned=True,
                                ),
                            },
                            {"role": "assistant", "content": "round-1 answer"},
                            {
                                "role": "user",
                                "content": _wrapped_feishu_user_text(
                                    "Test Bot dm sse round-2 latest",
                                    message_id="om_sse_2",
                                    group=True,
                                    mentioned=True,
                                ),
                            },
                        ],
                        "stream": True,
                        "metadata": {
                            "source": "openclaw-feishu",
                            "chat_type": "p2p",
                            "conversation_id": "oc-feishu-sse-dm-001",
                        },
                    },
                ) as response:
                    lines = [line async for line in response.aiter_lines() if line]

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["X-Request-Id"]
        assert captured_prompts == ["dm sse round-2 latest"]
        assert lines[-1] == "data: [DONE]"
        first_payload = json.loads(lines[0][len("data: "):])
        finish_payload = json.loads(lines[-2][len("data: "):])
        assert first_payload["object"] == "chat.completion.chunk"
        assert first_payload["choices"][0]["delta"]["content"] == "Fei"
        assert finish_payload["choices"][0]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_feishu_stream_endpoint_strips_real_transport_envelope_from_content_parts(
        self,
    ):
        captured_prompts = []

        def _stream_events(*args, **kwargs):
            captured_prompts.append(args[0])
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
        ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
            "api_server.main.compute_client.process_stream",
            side_effect=_stream_events,
        ):
            from httpx import AsyncClient, ASGITransport

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
                                "content": build_real_openclaw_feishu_content_parts(
                                    "stream real envelope round-1",
                                    message_id="om_real_sse_1",
                                    transport_timestamp="2026-04-16 09:37:07 GMT+8",
                                    message_timestamp="Thu 2026-04-16 09:36 GMT+8",
                                ),
                            },
                            {"role": "assistant", "content": "round-1 answer"},
                            {
                                "role": "user",
                                "content": build_real_openclaw_feishu_content_parts(
                                    "stream real envelope round-2 latest",
                                    message_id="om_real_sse_2",
                                    transport_timestamp="2026-04-16 10:35:31 GMT+8",
                                    message_timestamp="Thu 2026-04-16 10:30 GMT+8",
                                ),
                            },
                        ],
                        "stream": True,
                        "metadata": {
                            "source": "openclaw-feishu",
                            "chat_type": "p2p",
                            "conversation_id": "oc-feishu-sse-real-envelope-001",
                        },
                    },
                ) as response:
                    lines = [line async for line in response.aiter_lines() if line]

        assert response.status_code == 200
        assert captured_prompts == ["stream real envelope round-2 latest"]
        assert lines[-1] == "data: [DONE]"

    @pytest.mark.asyncio
    async def test_feishu_stream_endpoint_returns_stable_error_event(self):
        def _stream_events(*args, **kwargs):
            yield SimpleNamespace(
                content="",
                is_final=False,
                error_message="upstream stream interrupted",
                completion_status="backend_error",
                completion_detail="",
            )

        with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
            "api_server.main._ensure_model_available"
        ), patch("api_server.main._get_model_record", return_value=_policy_record()), patch(
            "api_server.main.compute_client.process_stream",
            side_effect=_stream_events,
        ):
            from httpx import AsyncClient, ASGITransport

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [
                            {"role": "user", "content": "@ruyi-bot group err round-1"},
                            {"role": "assistant", "content": "group err answer"},
                            {"role": "user", "content": "@ruyi-bot group err round-2 latest"},
                        ],
                        "stream": True,
                        "metadata": {
                            "source": "openclaw-feishu",
                            "chat_type": "group",
                            "conversation_id": "oc-feishu-sse-group-001",
                            "mentions": ["ruyi-bot"],
                        },
                    },
                ) as response:
                    lines = [line async for line in response.aiter_lines() if line]

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["X-Request-Id"]
        assert len(lines) == 1
        payload = json.loads(lines[0][len("data: "):])
        assert payload["error"]["type"] == "server_error"
        assert payload["error"]["code"] == "backend_error"
        assert "upstream stream interrupted" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# Tests: Endpoint integration — Request object is passed (Req 6.4)
# ---------------------------------------------------------------------------


class TestEndpointPassesRequestObject:
    """Test that the /v1/chat/completions and /v1/completions endpoints
    pass the HTTP Request object to the streaming generators."""

    @pytest.mark.asyncio
    async def test_chat_endpoint_passes_request_to_stream(self):
        """The chat completions endpoint should pass the Request object
        to _stream_chat_response when streaming is enabled."""
        from httpx import AsyncClient, ASGITransport
        import api_server.main as main_module

        # Mock compute_client.process_stream to yield one chunk
        def _mock_stream(*args, **kwargs):
            yield "test response"

        async def _mock_ensure_model_available(model_id):
            return None

        with patch.object(main_module, "auth_manager", _disabled_auth_manager()), \
             patch.object(main_module.compute_client, "process_stream", side_effect=_mock_stream), \
             patch.object(main_module, "_get_model_record", return_value=None), \
             patch.object(main_module, "_ensure_model_available", side_effect=_mock_ensure_model_available):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    },
                )
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")

    @pytest.mark.asyncio
    async def test_completion_endpoint_passes_request_to_stream(self):
        """The completions endpoint should pass the Request object
        to _stream_completion_response when streaming is enabled."""
        from httpx import AsyncClient, ASGITransport
        import api_server.main as main_module

        def _mock_stream(*args, **kwargs):
            yield "test output"

        async def _mock_ensure_model_available(model_id):
            return None

        with patch.object(main_module, "auth_manager", _disabled_auth_manager()), \
             patch.object(main_module.compute_client, "process_stream", side_effect=_mock_stream), \
             patch.object(main_module, "_get_model_record", return_value=None), \
             patch.object(main_module, "_ensure_model_available", side_effect=_mock_ensure_model_available):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1/completions",
                    json={
                        "model": "test-model",
                        "prompt": "hello",
                        "stream": True,
                    },
                )
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
