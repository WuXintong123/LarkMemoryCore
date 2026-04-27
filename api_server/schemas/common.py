"""Common schema fragments."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator


def _normalize_message_content_payload(
    raw_content: Any,
) -> Tuple[str, List[str]]:
    if raw_content is None:
        return "", []
    if isinstance(raw_content, str):
        return raw_content, []
    if not isinstance(raw_content, list):
        return str(raw_content), []

    text_parts: List[str] = []
    unsupported_types: List[str] = []
    for part in raw_content:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            unsupported_types.append(type(part).__name__)
            continue

        part_type = part.get("type")
        if part_type in (None, "text", "input_text"):
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
                continue
            if isinstance(part.get("input_text"), str):
                text_parts.append(part["input_text"])
                continue
            if part_type is None and isinstance(part.get("content"), str):
                text_parts.append(part["content"])
                continue

        unsupported_types.append(str(part_type or "unknown"))

    return "".join(text_parts), unsupported_types


class Message(BaseModel):
    model_config = {"extra": "ignore"}

    role: str = Field(
        ...,
        description="Message role. Supported values: system, user, assistant, tool, developer.",
        examples=["user"],
    )
    content: str = Field(
        ...,
        description="Raw message content passed into the prompt renderer.",
        examples=["Say READY only."],
    )
    tool_calls: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        exclude=True,
        description="Optional assistant tool calls preserved for prompt rendering.",
    )
    tool_call_id: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Optional tool call correlation id preserved for prompt rendering.",
    )
    unsupported_content_types: List[str] = Field(
        default_factory=list,
        exclude=True,
        description="Non-text content part types observed during compatibility normalization.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_openai_compatible_content(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        should_normalize_content = "content" in normalized or (
            normalized.get("role") == "assistant" and normalized.get("tool_calls")
        )
        if not should_normalize_content:
            return normalized

        content, unsupported_types = _normalize_message_content_payload(
            normalized.get("content")
        )
        normalized["content"] = content
        if unsupported_types:
            normalized["unsupported_content_types"] = unsupported_types
        return normalized
