"""Response schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .common import Message


class ChatCompletionChoice(BaseModel):
    index: int = Field(..., description="Choice index.")
    message: Message = Field(..., description="Assistant message payload.")
    finish_reason: Optional[str] = Field(default="stop", description="stop or length depending on model termination.")


class CompletionChoice(BaseModel):
    index: int = Field(..., description="Choice index.")
    text: str = Field(..., description="Generated text for this choice.")
    finish_reason: Optional[str] = Field(default="stop", description="stop or length depending on model termination.")


class Usage(BaseModel):
    prompt_tokens: int = Field(..., description="Prompt token count as reported by the compute layer.")
    completion_tokens: int = Field(..., description="Completion token count as reported by the compute layer.")
    total_tokens: int = Field(..., description="Sum of prompt_tokens and completion_tokens.")


class ChatCompletionResponse(BaseModel):
    id: str = Field(..., description="Stable request identifier. Also returned in X-Request-Id.")
    object: str = "chat.completion"
    created: int = Field(..., description="Unix timestamp when the response was created.")
    model: str = Field(..., description="Model ID that served the request.")
    choices: List[ChatCompletionChoice] = Field(..., description="Completion choices.")
    usage: Usage = Field(..., description="Prompt/completion token accounting.")


class CompletionResponse(BaseModel):
    id: str = Field(..., description="Stable request identifier. Also returned in X-Request-Id.")
    object: str = "text_completion"
    created: int = Field(..., description="Unix timestamp when the response was created.")
    model: str = Field(..., description="Model ID that served the request.")
    choices: List[CompletionChoice] = Field(..., description="Completion choices.")
    usage: Usage = Field(..., description="Prompt/completion token accounting.")
