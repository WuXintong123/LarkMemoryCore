# ===- chat_template.py --------------------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Chat template engine for formatting multi-turn conversations into model input.
# Supports configurable role markers with default ChatML format.
#
# ===---------------------------------------------------------------------------

import os
from dataclasses import dataclass, field
from typing import List, Any


def _env_or_default(env_var: str, default: str) -> str:
    """Read a value from environment variable, falling back to default."""
    return os.getenv(env_var, default)


@dataclass
class ChatTemplate:
    """
    Configurable chat template for formatting multi-turn conversations.

    Default format uses ChatML-style markers:
        <|im_start|>system\n{content}<|im_end|>\n
        <|im_start|>user\n{content}<|im_end|>\n
        <|im_start|>assistant\n{content}<|im_end|>\n

    All markers can be customized via environment variables:
        CHAT_TEMPLATE_SYSTEM_PREFIX
        CHAT_TEMPLATE_SYSTEM_SUFFIX
        CHAT_TEMPLATE_USER_PREFIX
        CHAT_TEMPLATE_USER_SUFFIX
        CHAT_TEMPLATE_ASSISTANT_PREFIX
        CHAT_TEMPLATE_ASSISTANT_SUFFIX
        CHAT_TEMPLATE_TOOL_PREFIX
        CHAT_TEMPLATE_TOOL_SUFFIX
        CHAT_TEMPLATE_DEVELOPER_PREFIX
        CHAT_TEMPLATE_DEVELOPER_SUFFIX
        CHAT_TEMPLATE_GENERATION_PROMPT
    """

    system_prefix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_SYSTEM_PREFIX", "<|im_start|>system\n"
        )
    )
    system_suffix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_SYSTEM_SUFFIX", "<|im_end|>\n"
        )
    )
    user_prefix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_USER_PREFIX", "<|im_start|>user\n"
        )
    )
    user_suffix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_USER_SUFFIX", "<|im_end|>\n"
        )
    )
    assistant_prefix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_ASSISTANT_PREFIX", "<|im_start|>assistant\n"
        )
    )
    assistant_suffix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_ASSISTANT_SUFFIX", "<|im_end|>\n"
        )
    )
    tool_prefix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_TOOL_PREFIX", "<|im_start|>tool\n"
        )
    )
    tool_suffix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_TOOL_SUFFIX", "<|im_end|>\n"
        )
    )
    developer_prefix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_DEVELOPER_PREFIX", "<|im_start|>system\n"
        )
    )
    developer_suffix: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_DEVELOPER_SUFFIX", "<|im_end|>\n"
        )
    )
    generation_prompt: str = field(
        default_factory=lambda: _env_or_default(
            "CHAT_TEMPLATE_GENERATION_PROMPT", "<|im_start|>assistant\n"
        )
    )

    def format_messages(self, messages: List[Any]) -> str:
        """
        Format a list of messages into a model input string.

        Messages are formatted in their original order, preserving the
        conversation flow. Each message is wrapped with the appropriate
        role-specific prefix and suffix markers. A generation prompt is
        appended at the end to signal the model to generate a response.

        Args:
            messages: List of message objects with .role and .content attributes.
                      Supported roles: "system", "user", "assistant", "tool",
                      "developer".

        Returns:
            Formatted prompt string ready for model input.
        """
        parts = []
        for msg in messages:
            if msg.role == "system":
                parts.append(
                    f"{self.system_prefix}{msg.content}{self.system_suffix}"
                )
            elif msg.role == "user":
                parts.append(
                    f"{self.user_prefix}{msg.content}{self.user_suffix}"
                )
            elif msg.role == "assistant":
                parts.append(
                    f"{self.assistant_prefix}{msg.content}{self.assistant_suffix}"
                )
            elif msg.role == "tool":
                parts.append(
                    f"{self.tool_prefix}{msg.content}{self.tool_suffix}"
                )
            elif msg.role == "developer":
                parts.append(
                    f"{self.developer_prefix}{msg.content}{self.developer_suffix}"
                )
        # Append generation prompt at the end to signal model to generate
        parts.append(self.generation_prompt)
        return "".join(parts)


def format_buddy_deepseek_r1_messages(messages: List[Any]) -> str:
    """Render a Buddy-friendly plain-text transcript for chat inference."""
    role_labels = {
        "system": "System",
        "developer": "Developer",
        "user": "User",
        "assistant": "Assistant",
        "tool": "Tool",
    }
    parts = []
    for msg in messages:
        label = role_labels.get(msg.role, msg.role.capitalize())
        parts.append(f"{label}: {msg.content}")
    return "\n\n".join(parts)
