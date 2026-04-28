"""Request schemas."""

from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, Field

from .common import Message


class ChatCompletionRequest(BaseModel):
    model_config = {"extra": "ignore"}

    model: str = Field(
        ...,
        description="Model ID returned by /v1/models.",
        examples=["deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"],
    )
    messages: List[Message] = Field(
        ...,
        description="Ordered conversation history. At least one user message is required.",
    )
    temperature: Optional[float] = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. Defaults to 1.0.",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum completion tokens. Falls back to the model policy default when omitted.",
    )
    top_p: Optional[float] = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling threshold. Defaults to 1.0.",
    )
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        description="Top-k sampling cutoff when supported by the backend.",
    )
    repetition_penalty: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Penalty factor applied to repeated tokens.",
    )
    frequency_penalty: Optional[float] = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        description="Accepted by schema but explicitly rejected by LarkMemoryCore for compatibility clarity.",
    )
    presence_penalty: Optional[float] = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        description="Accepted by schema but explicitly rejected by LarkMemoryCore for compatibility clarity.",
    )
    seed: Optional[int] = Field(default=None, description="Optional deterministic sampling seed.")
    stream: Optional[bool] = Field(default=False, description="Enable streaming SSE responses.")
    n: Optional[int] = Field(
        default=1,
        ge=1,
        le=1,
        description="LarkMemoryCore currently supports n=1 only.",
    )
    stop: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="Accepted by schema but explicitly rejected by LarkMemoryCore for compatibility clarity.",
    )
    user: Optional[str] = Field(
        default=None,
        description="Optional caller identifier preserved for compatibility.",
    )


class CompletionRequest(BaseModel):
    model_config = {"extra": "ignore"}

    model: str = Field(
        ...,
        description="Model ID returned by /v1/models.",
        examples=["qwen/Qwen2.5-7B-Instruct"],
    )
    prompt: Union[str, List[str]] = Field(
        ...,
        description="A single prompt string or a list of prompt strings.",
        examples=["Write a concise summary of RISC-V."],
    )
    temperature: Optional[float] = Field(default=1.0, ge=0.0, le=2.0, description="Sampling temperature. Defaults to 1.0.")
    max_tokens: Optional[int] = Field(default=16, ge=1, description="Maximum completion tokens.")
    top_p: Optional[float] = Field(default=1.0, ge=0.0, le=1.0, description="Nucleus sampling threshold.")
    top_k: Optional[int] = Field(default=None, ge=1, description="Top-k sampling cutoff when supported by the backend.")
    repetition_penalty: Optional[float] = Field(default=None, ge=0.0, description="Penalty factor applied to repeated tokens.")
    frequency_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0, description="Accepted by schema but explicitly rejected by LarkMemoryCore.")
    presence_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0, description="Accepted by schema but explicitly rejected by LarkMemoryCore.")
    seed: Optional[int] = Field(default=None, description="Optional deterministic sampling seed.")
    stream: Optional[bool] = Field(default=False, description="Enable streaming SSE responses. Only one prompt is allowed when stream=true.")
    n: Optional[int] = Field(default=1, ge=1, le=1, description="LarkMemoryCore currently supports n=1 only.")
    stop: Optional[Union[str, List[str]]] = Field(default=None, description="Accepted by schema but explicitly rejected by LarkMemoryCore.")
    echo: Optional[bool] = Field(default=False, description="When true, echo the prompt back in each completion choice.")
    user: Optional[str] = Field(default=None, description="Optional caller identifier preserved for compatibility.")


class RegisterModelRequest(BaseModel):
    id: str = Field(..., description="Model ID")
    owned_by: Optional[str] = Field(default="lark-memory-core", description="Model owner")
    created: Optional[int] = Field(default=None, description="Creation timestamp")


class CancelRequest(BaseModel):
    request_id: str = Field(
        ...,
        description="Request ID to cancel. This matches the X-Request-Id header returned for inference requests.",
    )
