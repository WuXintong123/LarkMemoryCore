# ===- test_input_validation.py ------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Unit tests for input validation functions in api_server/main.py.
# Tests validate_chat_request and validate_completion_request functions.
#
# Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
#
# ===---------------------------------------------------------------------------

import os
import sys

import pytest
from fastapi import HTTPException

# Ensure the project root is on sys.path so that api_server can be imported.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.main import (
    validate_chat_request,
    validate_completion_request,
    ChatCompletionRequest,
    CompletionRequest,
    Message,
    MAX_CONTENT_LENGTH,
    VALID_ROLES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chat_request(messages=None, model="test-model", **kwargs):
    """Create a ChatCompletionRequest with the given messages."""
    if messages is None:
        messages = [{"role": "user", "content": "Hello"}]
    return ChatCompletionRequest(model=model, messages=messages, **kwargs)


def _make_completion_request(prompt="Hello", model="test-model", **kwargs):
    """Create a CompletionRequest with the given prompt."""
    return CompletionRequest(model=model, prompt=prompt, **kwargs)


# ---------------------------------------------------------------------------
# Tests for validate_chat_request
# ---------------------------------------------------------------------------

class TestValidateChatRequestEmptyMessages:
    """Requirement 4.1: messages must not be empty"""

    def test_empty_messages_list_raises_400(self):
        """An empty messages list should raise HTTPException with status 400."""
        request = _make_chat_request(messages=[])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "messages must not be empty" in error_detail["error"]["message"]


class TestValidateChatRequestUserMessage:
    """Requirement 4.2: at least one user message is required"""

    def test_no_user_message_raises_400(self):
        """Messages with only system and assistant roles should raise HTTPException."""
        request = _make_chat_request(messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "assistant", "content": "Hello!"},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "At least one user message is required" in error_detail["error"]["message"]

    def test_only_system_messages_raises_400(self):
        """Messages with only system role should raise HTTPException."""
        request = _make_chat_request(messages=[
            {"role": "system", "content": "System prompt"},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert "At least one user message is required" in error_detail["error"]["message"]

    def test_only_assistant_messages_raises_400(self):
        """Messages with only assistant role should raise HTTPException."""
        request = _make_chat_request(messages=[
            {"role": "assistant", "content": "I can help!"},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400

    def test_with_user_message_passes(self):
        """Messages containing at least one user message should pass validation."""
        request = _make_chat_request(messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ])
        # Should not raise
        validate_chat_request(request)


class TestValidateChatRequestRoleValidity:
    """Requirement 4.4: role must be one of system, user, assistant"""

    def test_invalid_role_raises_400(self):
        """A message with an invalid role should raise HTTPException."""
        request = _make_chat_request(messages=[
            {"role": "admin", "content": "Hello"},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "Invalid role" in error_detail["error"]["message"]
        assert "admin" in error_detail["error"]["message"]

    def test_function_role_raises_400(self):
        """The 'function' role is not in the valid set and should be rejected."""
        request = _make_chat_request(messages=[
            {"role": "user", "content": "Hello"},
            {"role": "function", "content": "result"},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert "function" in error_detail["error"]["message"]

    def test_empty_role_string_raises_400(self):
        """An empty string role should be rejected."""
        request = _make_chat_request(messages=[
            {"role": "", "content": "Hello"},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400

    def test_all_valid_roles_pass(self):
        """All three valid roles should pass validation when user is present."""
        request = _make_chat_request(messages=[
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
            {"role": "assistant", "content": "Assistant response"},
        ])
        # Should not raise
        validate_chat_request(request)


class TestValidateChatRequestContentLength:
    """Requirement 4.3: content must not exceed MAX_CONTENT_LENGTH"""

    def test_oversized_content_raises_400(self):
        """Content exceeding MAX_CONTENT_LENGTH should raise HTTPException."""
        oversized_content = "A" * (MAX_CONTENT_LENGTH + 1)
        request = _make_chat_request(messages=[
            {"role": "user", "content": oversized_content},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "maximum length" in error_detail["error"]["message"]
        assert str(MAX_CONTENT_LENGTH) in error_detail["error"]["message"]

    def test_content_at_exact_limit_passes(self):
        """Content at exactly MAX_CONTENT_LENGTH should pass validation."""
        exact_content = "A" * MAX_CONTENT_LENGTH
        request = _make_chat_request(messages=[
            {"role": "user", "content": exact_content},
        ])
        # Should not raise
        validate_chat_request(request)

    def test_oversized_system_message_raises_400(self):
        """Oversized content in a system message should also be rejected."""
        oversized_content = "B" * (MAX_CONTENT_LENGTH + 1)
        request = _make_chat_request(messages=[
            {"role": "system", "content": oversized_content},
            {"role": "user", "content": "Hello"},
        ])
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400

    def test_normal_content_passes(self):
        """Normal-length content should pass validation."""
        request = _make_chat_request(messages=[
            {"role": "user", "content": "Hello, how are you?"},
        ])
        # Should not raise
        validate_chat_request(request)


class TestValidateChatRequestValidInput:
    """Tests for valid chat requests that should pass all validation."""

    def test_simple_user_message(self):
        """A simple user message should pass validation."""
        request = _make_chat_request(messages=[
            {"role": "user", "content": "Hello"},
        ])
        validate_chat_request(request)

    def test_multi_turn_conversation(self):
        """A multi-turn conversation with all valid roles should pass."""
        request = _make_chat_request(messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
            {"role": "user", "content": "Tell me more."},
        ])
        validate_chat_request(request)


# ---------------------------------------------------------------------------
# Tests for validate_completion_request
# ---------------------------------------------------------------------------

class TestValidateCompletionRequestEmptyPrompt:
    """Requirement 4.5: prompt must not be empty"""

    def test_empty_string_prompt_raises_400(self):
        """An empty string prompt should raise HTTPException with status 400."""
        request = _make_completion_request(prompt="")
        with pytest.raises(HTTPException) as exc_info:
            validate_completion_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "prompt must not be empty" in error_detail["error"]["message"]

    def test_non_empty_string_prompt_passes(self):
        """A non-empty string prompt should pass validation."""
        request = _make_completion_request(prompt="Hello world")
        # Should not raise
        validate_completion_request(request)


class TestValidateCompletionRequestListPrompt:
    """Requirement 4.6: list prompt validation"""

    def test_empty_list_prompt_raises_400(self):
        """An empty list prompt should raise HTTPException with status 400."""
        request = _make_completion_request(prompt=[])
        with pytest.raises(HTTPException) as exc_info:
            validate_completion_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "prompt list must not be empty" in error_detail["error"]["message"]

    def test_list_with_empty_string_raises_400(self):
        """A list containing an empty string should raise HTTPException."""
        request = _make_completion_request(prompt=["Hello", ""])
        with pytest.raises(HTTPException) as exc_info:
            validate_completion_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "must not be empty" in error_detail["error"]["message"]

    def test_list_with_first_element_empty_raises_400(self):
        """A list where the first element is empty should raise HTTPException."""
        request = _make_completion_request(prompt=["", "Hello"])
        with pytest.raises(HTTPException) as exc_info:
            validate_completion_request(request)
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert "index 0" in error_detail["error"]["message"]

    def test_valid_list_prompt_passes(self):
        """A list of non-empty strings should pass validation."""
        request = _make_completion_request(prompt=["Hello", "World"])
        # Should not raise
        validate_completion_request(request)

    def test_single_element_list_passes(self):
        """A single-element list with a non-empty string should pass."""
        request = _make_completion_request(prompt=["Hello"])
        # Should not raise
        validate_completion_request(request)


class TestValidateCompletionRequestValidInput:
    """Tests for valid completion requests that should pass all validation."""

    def test_simple_string_prompt(self):
        """A simple string prompt should pass validation."""
        request = _make_completion_request(prompt="Tell me a story")
        validate_completion_request(request)

    def test_long_valid_prompt(self):
        """A long but valid prompt should pass validation."""
        request = _make_completion_request(prompt="A" * 10000)
        validate_completion_request(request)


class TestUnsupportedOpenAIParameters:
    """Unsupported parameters should be rejected explicitly (no silent ignore)."""

    def test_chat_frequency_penalty_rejected(self):
        request = _make_chat_request(frequency_penalty=0.1)
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail["error"]
        assert detail["code"] == "unsupported_parameter"
        assert detail["param"] == "frequency_penalty"

    def test_chat_presence_penalty_rejected(self):
        request = _make_chat_request(presence_penalty=0.1)
        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail["error"]
        assert detail["code"] == "unsupported_parameter"
        assert detail["param"] == "presence_penalty"

    def test_completion_stop_rejected(self):
        request = _make_completion_request(stop="END")
        with pytest.raises(HTTPException) as exc_info:
            validate_completion_request(request)
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail["error"]
        assert detail["code"] == "unsupported_parameter"
        assert detail["param"] == "stop"


# ---------------------------------------------------------------------------
# Property-Based Tests (hypothesis)
# ---------------------------------------------------------------------------
#
# Property 4: Chat request rejects messages without user role
# Property 5: Chat request rejects oversized content
# Property 6: Chat request rejects invalid roles
# Property 7: Completion request validates list prompts
#
# Validates: Requirements 4.2, 4.3, 4.4, 4.6
# ---------------------------------------------------------------------------

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for generating non-empty content strings for messages.
# Uses printable characters to avoid Pydantic validation issues with
# control characters, while still exercising a wide range of inputs.
_content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "M", "N", "P", "S", "Z")),
    min_size=1,
    max_size=200,
)

# Strategy for generating roles that are valid but NOT "user".
# These are drawn from the set {"system", "assistant"} only.
_non_user_valid_role_strategy = st.sampled_from(["system", "assistant"])

# Strategy for generating a single message dict with a non-user valid role.
_non_user_message_strategy = st.fixed_dictionaries({
    "role": _non_user_valid_role_strategy,
    "content": _content_strategy,
})

# Strategy for generating invalid role strings — strings that are NOT in
# {"system", "user", "assistant"}. We generate arbitrary text and filter
# out the three valid roles.
_invalid_role_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
).filter(lambda r: r not in VALID_ROLES)

# Strategy for generating content that exceeds MAX_CONTENT_LENGTH.
# We generate a length that is strictly greater than the limit, then
# build a string of that length.
_oversized_length_strategy = st.integers(
    min_value=MAX_CONTENT_LENGTH + 1,
    max_value=MAX_CONTENT_LENGTH + 500,
)

# Strategy for generating a valid role for the oversized content test.
_any_valid_role_strategy = st.sampled_from(["system", "user", "assistant"])

# Strategy for generating non-empty strings (for valid list prompt elements).
_nonempty_string_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=100,
)


# ---------------------------------------------------------------------------
# Property 4: Chat request rejects messages without user role
# ---------------------------------------------------------------------------

class TestProperty4ChatRequestRejectsMessagesWithoutUserRole:
    """Feature: serving-framework-enhancement, Property 4: Chat request rejects messages without user role"""

    @settings(max_examples=100)
    @given(
        messages=st.lists(
            _non_user_message_strategy,
            min_size=1,
            max_size=10,
        )
    )
    def test_property4_no_user_role_rejected(self, messages):
        """Feature: serving-framework-enhancement, Property 4: Chat request rejects messages without user role

        **Validates: Requirements 4.2**

        For any non-empty list of messages where no message has role "user"
        (all roles are drawn from {"system", "assistant"}), the chat request
        validator SHALL reject the request with an appropriate error.
        """
        # Precondition: no message has role "user"
        assert all(m["role"] in ("system", "assistant") for m in messages)

        request = _make_chat_request(messages=messages)

        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)

        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "At least one user message is required" in error_detail["error"]["message"]

    @settings(max_examples=100)
    @given(
        non_user_messages=st.lists(
            _non_user_message_strategy,
            min_size=0,
            max_size=5,
        ),
        user_content=_content_strategy,
        extra_non_user=st.lists(
            _non_user_message_strategy,
            min_size=0,
            max_size=5,
        ),
    )
    def test_property4_with_user_role_accepted(
        self, non_user_messages, user_content, extra_non_user
    ):
        """Feature: serving-framework-enhancement, Property 4: Chat request rejects messages without user role

        **Validates: Requirements 4.2**

        Converse check: for any list of messages that includes at least one
        message with role "user" (and all roles are valid), the chat request
        validator SHALL accept the request (not raise for the user-role check).
        """
        # Build a message list that includes at least one user message.
        messages = (
            non_user_messages
            + [{"role": "user", "content": user_content}]
            + extra_non_user
        )

        # Ensure all content is within limits so we only test the user-role property.
        assume(all(len(m["content"]) <= MAX_CONTENT_LENGTH for m in messages))

        request = _make_chat_request(messages=messages)

        # Should NOT raise — the request has a user message and all roles are valid.
        validate_chat_request(request)


# ---------------------------------------------------------------------------
# Property 5: Chat request rejects oversized content
# ---------------------------------------------------------------------------

class TestProperty5ChatRequestRejectsOversizedContent:
    """Feature: serving-framework-enhancement, Property 5: Chat request rejects oversized content"""

    @settings(max_examples=100)
    @given(
        role=_any_valid_role_strategy,
        oversized_length=_oversized_length_strategy,
        fill_char=st.sampled_from(["A", "B", "x", "Z", "1", "好", "🚀"]),
    )
    def test_property5_oversized_content_rejected(
        self, role, oversized_length, fill_char
    ):
        """Feature: serving-framework-enhancement, Property 5: Chat request rejects oversized content

        **Validates: Requirements 4.3**

        For any message whose content length exceeds the configured
        MAX_CONTENT_LENGTH, the chat request validator SHALL reject the request
        with an error indicating the content length limit.
        """
        oversized_content = fill_char * oversized_length
        assert len(oversized_content) > MAX_CONTENT_LENGTH

        # Build a valid message list: always include a user message so that
        # the validator reaches the content-length check.
        if role == "user":
            messages = [{"role": "user", "content": oversized_content}]
        else:
            messages = [
                {"role": role, "content": oversized_content},
                {"role": "user", "content": "valid user message"},
            ]

        request = _make_chat_request(messages=messages)

        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)

        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "maximum length" in error_detail["error"]["message"]
        assert str(MAX_CONTENT_LENGTH) in error_detail["error"]["message"]

    @settings(max_examples=100)
    @given(
        content_length=st.integers(min_value=1, max_value=MAX_CONTENT_LENGTH),
    )
    def test_property5_within_limit_accepted(self, content_length):
        """Feature: serving-framework-enhancement, Property 5: Chat request rejects oversized content

        **Validates: Requirements 4.3**

        Converse check: for any message whose content length is at or below
        MAX_CONTENT_LENGTH, the chat request validator SHALL NOT reject the
        request for content length reasons.
        """
        content = "A" * content_length
        assert len(content) <= MAX_CONTENT_LENGTH

        messages = [{"role": "user", "content": content}]
        request = _make_chat_request(messages=messages)

        # Should NOT raise — content is within the limit.
        validate_chat_request(request)


# ---------------------------------------------------------------------------
# Property 6: Chat request rejects invalid roles
# ---------------------------------------------------------------------------

class TestProperty6ChatRequestRejectsInvalidRoles:
    """Feature: serving-framework-enhancement, Property 6: Chat request rejects invalid roles"""

    @settings(max_examples=100)
    @given(
        invalid_role=_invalid_role_strategy,
        content=_content_strategy,
    )
    def test_property6_invalid_role_rejected(self, invalid_role, content):
        """Feature: serving-framework-enhancement, Property 6: Chat request rejects invalid roles

        **Validates: Requirements 4.4**

        For any message whose role is a string not in the set
        {"system", "user", "assistant"}, the chat request validator SHALL
        reject the request with an error listing the valid roles.
        """
        assert invalid_role not in VALID_ROLES

        # Ensure content is within limits so we isolate the role check.
        assume(len(content) <= MAX_CONTENT_LENGTH)

        messages = [{"role": invalid_role, "content": content}]
        request = _make_chat_request(messages=messages)

        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)

        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        # The error message must mention the invalid role.
        assert invalid_role in error_detail["error"]["message"]
        # The error message must list the valid roles.
        assert "Invalid role" in error_detail["error"]["message"]

    @settings(max_examples=100)
    @given(
        invalid_role=_invalid_role_strategy,
        valid_messages=st.lists(
            st.fixed_dictionaries({
                "role": _any_valid_role_strategy,
                "content": _content_strategy,
            }),
            min_size=0,
            max_size=5,
        ),
        content=_content_strategy,
    )
    def test_property6_invalid_role_among_valid_messages_rejected(
        self, invalid_role, valid_messages, content
    ):
        """Feature: serving-framework-enhancement, Property 6: Chat request rejects invalid roles

        **Validates: Requirements 4.4**

        Even when an invalid role appears among otherwise valid messages, the
        chat request validator SHALL reject the request.
        """
        assert invalid_role not in VALID_ROLES

        # Ensure all content is within limits.
        assume(len(content) <= MAX_CONTENT_LENGTH)
        assume(all(len(m["content"]) <= MAX_CONTENT_LENGTH for m in valid_messages))

        # Place the invalid-role message at the beginning so the validator
        # encounters it during its role-validity scan.
        messages = [{"role": invalid_role, "content": content}] + valid_messages
        request = _make_chat_request(messages=messages)

        with pytest.raises(HTTPException) as exc_info:
            validate_chat_request(request)

        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert invalid_role in error_detail["error"]["message"]


# ---------------------------------------------------------------------------
# Property 7: Completion request validates list prompts
# ---------------------------------------------------------------------------

class TestProperty7CompletionRequestValidatesListPrompts:
    """Feature: serving-framework-enhancement, Property 7: Completion request validates list prompts"""

    @settings(max_examples=100)
    @given(data=st.data())
    def test_property7_empty_list_rejected(self, data):
        """Feature: serving-framework-enhancement, Property 7: Completion request validates list prompts

        **Validates: Requirements 4.6**

        For any prompt provided as an empty list, the completion request
        validator SHALL reject the request with an appropriate error.
        """
        request = _make_completion_request(prompt=[])

        with pytest.raises(HTTPException) as exc_info:
            validate_completion_request(request)

        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "prompt list must not be empty" in error_detail["error"]["message"]

    @settings(max_examples=100)
    @given(
        prefix_elements=st.lists(
            _nonempty_string_strategy,
            min_size=0,
            max_size=5,
        ),
        suffix_elements=st.lists(
            _nonempty_string_strategy,
            min_size=0,
            max_size=5,
        ),
    )
    def test_property7_list_with_empty_string_rejected(
        self, prefix_elements, suffix_elements
    ):
        """Feature: serving-framework-enhancement, Property 7: Completion request validates list prompts

        **Validates: Requirements 4.6**

        For any prompt provided as a list that contains at least one empty
        string, the completion request validator SHALL reject the request
        with an appropriate error.
        """
        # Insert an empty string between prefix and suffix elements.
        prompt_list = prefix_elements + [""] + suffix_elements
        assert "" in prompt_list

        request = _make_completion_request(prompt=prompt_list)

        with pytest.raises(HTTPException) as exc_info:
            validate_completion_request(request)

        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        assert error_detail["error"]["type"] == "invalid_request_error"
        assert "must not be empty" in error_detail["error"]["message"]

    @settings(max_examples=100)
    @given(
        elements=st.lists(
            _nonempty_string_strategy,
            min_size=1,
            max_size=10,
        ),
    )
    def test_property7_valid_list_accepted(self, elements):
        """Feature: serving-framework-enhancement, Property 7: Completion request validates list prompts

        **Validates: Requirements 4.6**

        Converse check: for any prompt provided as a non-empty list where
        every element is a non-empty string, the completion request validator
        SHALL accept the request.
        """
        # Precondition: all elements are non-empty strings.
        assert all(len(e) > 0 for e in elements)

        request = _make_completion_request(prompt=elements)

        # Should NOT raise — the list is valid.
        validate_completion_request(request)
