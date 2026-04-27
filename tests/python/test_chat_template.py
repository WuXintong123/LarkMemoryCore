# ===- test_chat_template.py ---------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Property-based and unit tests for the ChatTemplate class from
# api_server/chat_template.py.
#
# Property 13: Chat template preserves message order
# Property 14: Chat template applies correct role markers
# Validates: Requirements 8.2, 8.1, 8.4
#
# ===---------------------------------------------------------------------------

import os
import sys
from dataclasses import dataclass

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Ensure the project root is on sys.path so that api_server can be imported.
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from api_server.chat_template import ChatTemplate


# ---------------------------------------------------------------------------
# Test message dataclass
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    """Simple message object with .role and .content attributes for testing."""
    role: str
    content: str


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid roles for chat messages.
_valid_roles = ["system", "user", "assistant"]
_valid_role_strategy = st.sampled_from(_valid_roles)

# Strategy for generating message content strings.
# Uses printable characters, avoids control characters. Content must be
# non-empty so that we can meaningfully locate it in the formatted output.
# We also exclude characters that appear in ChatML markers to avoid
# ambiguous position lookups.
_content_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "M", "N", "P", "S", "Z"),
        blacklist_characters="<|>",
    ),
    min_size=1,
    max_size=200,
)

# Strategy for generating a single test message with a valid role.
_message_strategy = st.builds(
    ChatMessage,
    role=_valid_role_strategy,
    content=_content_strategy,
)

# Strategy for generating a non-empty list of messages.
_messages_list_strategy = st.lists(
    _message_strategy,
    min_size=1,
    max_size=15,
)

# Strategy for generating custom prefix/suffix strings for ChatTemplate.
# These must be non-empty and should not overlap with typical content.
_marker_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters="<|>",
    ),
    min_size=1,
    max_size=30,
)


# ---------------------------------------------------------------------------
# Property 13: Chat template preserves message order
# ---------------------------------------------------------------------------

class TestProperty13ChatTemplatePreservesMessageOrder:
    """Feature: serving-framework-enhancement, Property 13: Chat template preserves message order"""

    @settings(max_examples=100)
    @given(messages=_messages_list_strategy)
    def test_property13_message_order_preserved(self, messages):
        """Feature: serving-framework-enhancement, Property 13: Chat template preserves message order

        **Validates: Requirements 8.2**

        For any ordered list of messages with valid roles, the formatted output
        from ChatTemplate.format_messages() SHALL contain each message's content
        in the same relative order as the input list (i.e., for any two messages
        m_i and m_j where i < j, the position of m_i's content in the output
        precedes the position of m_j's content).
        """
        template = ChatTemplate()
        output = template.format_messages(messages)

        # Verify that each message's content appears in the output and that
        # the relative order is preserved.
        last_pos = -1
        for i, msg in enumerate(messages):
            pos = output.find(msg.content, last_pos + 1)
            assert pos != -1, (
                f"Message {i} content {msg.content!r} not found in output "
                f"after position {last_pos}"
            )
            assert pos > last_pos, (
                f"Message {i} content at position {pos} does not follow "
                f"message {i-1} content at position {last_pos}"
            )
            last_pos = pos

    @settings(max_examples=100)
    @given(messages=st.lists(_message_strategy, min_size=2, max_size=10))
    def test_property13_pairwise_order_preserved(self, messages):
        """Feature: serving-framework-enhancement, Property 13: Chat template preserves message order

        **Validates: Requirements 8.2**

        For any pair of messages m_i and m_j where i < j, the position of
        m_i's content in the formatted output SHALL precede the position of
        m_j's content.
        """
        # Ensure all contents are unique so positions are unambiguous.
        contents = [m.content for m in messages]
        assume(len(set(contents)) == len(contents))

        template = ChatTemplate()
        output = template.format_messages(messages)

        # To avoid false matches where short content strings appear inside
        # ChatML markers, we locate each message by searching for its full
        # wrapped block (prefix + content + suffix).
        role_to_markers = {
            "system": (template.system_prefix, template.system_suffix),
            "user": (template.user_prefix, template.user_suffix),
            "assistant": (template.assistant_prefix, template.assistant_suffix),
        }

        # Check all pairs (i, j) where i < j.
        for i in range(len(messages)):
            prefix_i, suffix_i = role_to_markers[messages[i].role]
            block_i = f"{prefix_i}{messages[i].content}{suffix_i}"
            pos_i = output.find(block_i)
            assert pos_i != -1, (
                f"Message {i} block {block_i!r} not found in output"
            )
            for j in range(i + 1, len(messages)):
                prefix_j, suffix_j = role_to_markers[messages[j].role]
                block_j = f"{prefix_j}{messages[j].content}{suffix_j}"
                pos_j = output.find(block_j)
                assert pos_j != -1, (
                    f"Message {j} block {block_j!r} not found in output"
                )
                assert pos_i < pos_j, (
                    f"Order violation: message {i} (pos {pos_i}) should precede "
                    f"message {j} (pos {pos_j})"
                )


# ---------------------------------------------------------------------------
# Property 14: Chat template applies correct role markers
# ---------------------------------------------------------------------------

class TestProperty14ChatTemplateAppliesCorrectRoleMarkers:
    """Feature: serving-framework-enhancement, Property 14: Chat template applies correct role markers"""

    @settings(max_examples=100)
    @given(
        role=_valid_role_strategy,
        content=_content_strategy,
    )
    def test_property14_default_template_role_markers(self, role, content):
        """Feature: serving-framework-enhancement, Property 14: Chat template applies correct role markers

        **Validates: Requirements 8.1, 8.4**

        For any message with a valid role and the default ChatTemplate
        configuration, the formatted output SHALL contain the message's content
        preceded by the role-specific prefix and followed by the role-specific
        suffix as defined in the template.
        """
        template = ChatTemplate()
        msg = ChatMessage(role=role, content=content)
        output = template.format_messages([msg])

        # Determine the expected prefix and suffix for this role.
        if role == "system":
            expected_prefix = template.system_prefix
            expected_suffix = template.system_suffix
        elif role == "user":
            expected_prefix = template.user_prefix
            expected_suffix = template.user_suffix
        elif role == "assistant":
            expected_prefix = template.assistant_prefix
            expected_suffix = template.assistant_suffix

        # The formatted message block must be: prefix + content + suffix
        expected_block = f"{expected_prefix}{content}{expected_suffix}"
        assert expected_block in output, (
            f"Expected block {expected_block!r} not found in output {output!r}"
        )

    @settings(max_examples=100)
    @given(
        messages=st.lists(_message_strategy, min_size=1, max_size=10),
    )
    def test_property14_all_messages_have_correct_markers(self, messages):
        """Feature: serving-framework-enhancement, Property 14: Chat template applies correct role markers

        **Validates: Requirements 8.1, 8.4**

        For any list of messages with valid roles, every message in the
        formatted output SHALL be wrapped with the correct role-specific
        prefix and suffix.
        """
        template = ChatTemplate()
        output = template.format_messages(messages)

        role_to_markers = {
            "system": (template.system_prefix, template.system_suffix),
            "user": (template.user_prefix, template.user_suffix),
            "assistant": (template.assistant_prefix, template.assistant_suffix),
        }

        for i, msg in enumerate(messages):
            prefix, suffix = role_to_markers[msg.role]
            expected_block = f"{prefix}{msg.content}{suffix}"
            assert expected_block in output, (
                f"Message {i} (role={msg.role!r}, content={msg.content!r}): "
                f"expected block {expected_block!r} not found in output"
            )

    @settings(max_examples=100)
    @given(
        role=_valid_role_strategy,
        content=_content_strategy,
        sys_prefix=_marker_strategy,
        sys_suffix=_marker_strategy,
        usr_prefix=_marker_strategy,
        usr_suffix=_marker_strategy,
        ast_prefix=_marker_strategy,
        ast_suffix=_marker_strategy,
        gen_prompt=_marker_strategy,
    )
    def test_property14_custom_template_role_markers(
        self, role, content,
        sys_prefix, sys_suffix,
        usr_prefix, usr_suffix,
        ast_prefix, ast_suffix,
        gen_prompt,
    ):
        """Feature: serving-framework-enhancement, Property 14: Chat template applies correct role markers

        **Validates: Requirements 8.1, 8.4**

        For any message with a valid role and any ChatTemplate configuration,
        the formatted output SHALL contain the message's content preceded by
        the role-specific prefix and followed by the role-specific suffix as
        defined in the template.
        """
        template = ChatTemplate(
            system_prefix=sys_prefix,
            system_suffix=sys_suffix,
            user_prefix=usr_prefix,
            user_suffix=usr_suffix,
            assistant_prefix=ast_prefix,
            assistant_suffix=ast_suffix,
            generation_prompt=gen_prompt,
        )

        msg = ChatMessage(role=role, content=content)
        output = template.format_messages([msg])

        # Determine the expected prefix and suffix for this role.
        if role == "system":
            expected_prefix = sys_prefix
            expected_suffix = sys_suffix
        elif role == "user":
            expected_prefix = usr_prefix
            expected_suffix = usr_suffix
        elif role == "assistant":
            expected_prefix = ast_prefix
            expected_suffix = ast_suffix

        # The formatted message block must be: prefix + content + suffix
        expected_block = f"{expected_prefix}{content}{expected_suffix}"
        assert expected_block in output, (
            f"Expected block {expected_block!r} not found in output {output!r}"
        )

        # The generation prompt must appear at the end of the output.
        assert output.endswith(gen_prompt), (
            f"Output should end with generation prompt {gen_prompt!r}, "
            f"but ends with {output[-len(gen_prompt)-10:]!r}"
        )


# ---------------------------------------------------------------------------
# Unit Tests — edge cases and specific scenarios
# ---------------------------------------------------------------------------

class TestChatTemplateUnitTests:
    """Unit tests for ChatTemplate covering edge cases and specific scenarios."""

    def test_single_user_message_default_template(self):
        """A single user message should be formatted with ChatML markers."""
        template = ChatTemplate()
        msg = ChatMessage(role="user", content="Hello")
        output = template.format_messages([msg])

        expected = (
            "<|im_start|>user\nHello<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        assert output == expected

    def test_single_system_message_default_template(self):
        """A single system message should be formatted with system markers."""
        template = ChatTemplate()
        msg = ChatMessage(role="system", content="You are helpful.")
        output = template.format_messages([msg])

        expected = (
            "<|im_start|>system\nYou are helpful.<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        assert output == expected

    def test_multi_turn_conversation(self):
        """A multi-turn conversation should preserve order and apply correct markers."""
        template = ChatTemplate()
        messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="What is Python?"),
            ChatMessage(role="assistant", content="Python is a programming language."),
            ChatMessage(role="user", content="Tell me more."),
        ]
        output = template.format_messages(messages)

        expected = (
            "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n"
            "<|im_start|>user\nWhat is Python?<|im_end|>\n"
            "<|im_start|>assistant\nPython is a programming language.<|im_end|>\n"
            "<|im_start|>user\nTell me more.<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        assert output == expected

    def test_empty_messages_list(self):
        """An empty messages list should produce only the generation prompt."""
        template = ChatTemplate()
        output = template.format_messages([])

        assert output == "<|im_start|>assistant\n"

    def test_generation_prompt_appended(self):
        """The generation prompt should always be appended at the end."""
        template = ChatTemplate()
        msg = ChatMessage(role="user", content="Hi")
        output = template.format_messages([msg])

        assert output.endswith("<|im_start|>assistant\n")

    def test_custom_template_markers(self):
        """Custom template markers should be used in the formatted output."""
        template = ChatTemplate(
            system_prefix="[SYS]",
            system_suffix="[/SYS]",
            user_prefix="[USR]",
            user_suffix="[/USR]",
            assistant_prefix="[AST]",
            assistant_suffix="[/AST]",
            generation_prompt="[GEN]",
        )
        messages = [
            ChatMessage(role="system", content="Be helpful"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi there"),
        ]
        output = template.format_messages(messages)

        expected = "[SYS]Be helpful[/SYS][USR]Hello[/USR][AST]Hi there[/AST][GEN]"
        assert output == expected

    def test_message_with_unicode_content(self):
        """Messages with unicode content should be preserved correctly."""
        template = ChatTemplate()
        msg = ChatMessage(role="user", content="你好世界 🌍")
        output = template.format_messages([msg])

        assert "你好世界 🌍" in output
        assert "<|im_start|>user\n你好世界 🌍<|im_end|>\n" in output

    def test_message_with_newlines_in_content(self):
        """Messages with newlines in content should be preserved."""
        template = ChatTemplate()
        msg = ChatMessage(role="user", content="line1\nline2\nline3")
        output = template.format_messages([msg])

        assert "<|im_start|>user\nline1\nline2\nline3<|im_end|>\n" in output

    def test_multiple_same_role_messages(self):
        """Multiple messages with the same role should all be formatted correctly."""
        template = ChatTemplate()
        messages = [
            ChatMessage(role="user", content="First question"),
            ChatMessage(role="user", content="Second question"),
            ChatMessage(role="user", content="Third question"),
        ]
        output = template.format_messages(messages)

        assert "<|im_start|>user\nFirst question<|im_end|>\n" in output
        assert "<|im_start|>user\nSecond question<|im_end|>\n" in output
        assert "<|im_start|>user\nThird question<|im_end|>\n" in output

        # Verify order
        pos1 = output.find("First question")
        pos2 = output.find("Second question")
        pos3 = output.find("Third question")
        assert pos1 < pos2 < pos3

    def test_all_three_roles_present(self):
        """All three roles should be formatted with their respective markers."""
        template = ChatTemplate()
        messages = [
            ChatMessage(role="system", content="sys_content"),
            ChatMessage(role="user", content="usr_content"),
            ChatMessage(role="assistant", content="ast_content"),
        ]
        output = template.format_messages(messages)

        assert "<|im_start|>system\nsys_content<|im_end|>\n" in output
        assert "<|im_start|>user\nusr_content<|im_end|>\n" in output
        assert "<|im_start|>assistant\nast_content<|im_end|>\n" in output
