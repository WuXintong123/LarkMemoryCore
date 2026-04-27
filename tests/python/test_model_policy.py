# ===- test_model_policy.py ---------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Tests for model serving policy helpers and buddy prompt rendering.
#
# ===---------------------------------------------------------------------------

import os
import sys
from types import SimpleNamespace

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.chat_template import format_buddy_deepseek_r1_messages
from api_server.model_policy import ModelServingPolicy


def test_model_policy_parses_valid_payload():
    policy = ModelServingPolicy.from_payload(
        {
            "api_mode": "chat",
            "prompt_style": "buddy_deepseek_r1",
            "default_max_tokens": 64,
            "max_max_tokens": 128,
            "max_input_chars": 4096,
            "request_timeout_ms": 120000,
            "stream_idle_timeout_s": 20,
            "allow_anonymous_models": True,
        }
    )

    assert policy.api_mode == "chat"
    assert policy.prompt_style == "buddy_deepseek_r1"
    assert policy.default_max_tokens == 64
    assert policy.max_max_tokens == 128
    assert policy.max_input_chars == 4096
    assert policy.request_timeout_ms == 120000
    assert policy.stream_idle_timeout_s == 20
    assert policy.allow_anonymous_models is True


def test_model_policy_falls_back_for_invalid_values():
    policy = ModelServingPolicy.from_payload(
        {
            "api_mode": "invalid-mode",
            "prompt_style": "invalid-style",
            "default_max_tokens": -1,
        }
    )

    assert policy.api_mode == "both"
    assert policy.prompt_style == "chatml"
    assert policy.default_max_tokens == 0


def test_buddy_prompt_rendering_uses_plain_labels():
    messages = [
        SimpleNamespace(role="system", content="Stay concise."),
        SimpleNamespace(role="user", content="Say hello."),
        SimpleNamespace(role="assistant", content="Hello."),
    ]

    prompt = format_buddy_deepseek_r1_messages(messages)

    assert "System: Stay concise." in prompt
    assert "User: Say hello." in prompt
    assert "Assistant: Hello." in prompt
    assert "<|im_start|>" not in prompt
