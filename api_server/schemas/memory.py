"""Memory engine request and response schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MemoryEventInput(BaseModel):
    source: str = Field(
        ...,
        description="Source surface, for example openclaw-feishu, feishu, or document.",
    )
    tenant_id: str = Field(default="default", description="Tenant or installation scope.")
    project_id: Optional[str] = Field(
        default=None,
        description="Project scope. Defaults to the conversation id when omitted.",
    )
    conversation_id: str = Field(
        ...,
        description="Feishu/OpenClaw conversation id or a stable document scope.",
    )
    sender_id: Optional[str] = Field(default=None, description="Feishu sender id.")
    occurred_at: Optional[str] = Field(
        default=None,
        description="Event timestamp in ISO-8601 form. Missing values use ingestion time.",
    )
    raw_text: str = Field(..., min_length=1, description="Raw event or document text.")
    topic: Optional[str] = Field(
        default=None,
        description="Optional project topic used as the decision memory key.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw metadata from OpenClaw, Feishu, or document ingestion.",
    )


class MemoryEventIngestResponse(BaseModel):
    event_id: str
    created_count: int
    superseded_count: int
    active_memory_ids: List[str]
    ignored_reason: Optional[str] = None


class DecisionMemoryCard(BaseModel):
    id: str
    memory_key: str
    tenant_id: str
    project_id: str
    conversation_id: str
    topic: str
    decision: str
    reason: str
    objections: str
    conclusion: str
    status: str
    version: int
    source_event_id: str
    source_url: str
    occurred_at: str
    updated_at: str
    score: float = 0.0


class MemorySearchResponse(BaseModel):
    query: str
    hit_count: int
    cards: List[DecisionMemoryCard]
    metrics: Dict[str, Any]


class MemoryPromptComposition(BaseModel):
    prompt: str
    hit_count: int
    memory_ids: List[str]
    injected_characters: int
    saved_characters: int
    efficiency_gain_ratio: float
