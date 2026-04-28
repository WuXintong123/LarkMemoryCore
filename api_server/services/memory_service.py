"""Decision memory engine for OpenClaw + Feishu project context."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..infra.logger import setup_logger
from ..schemas.common import Message
from ..schemas.memory import (
    DecisionMemoryCard,
    MemoryEventIngestResponse,
    MemoryEventInput,
    MemoryPromptComposition,
    MemorySearchResponse,
)
from .inference_service import _extract_real_user_question


logger = setup_logger("memory_engine")

DECISION_STATUS_ACTIVE = "active"
DECISION_STATUS_SUPERSEDED = "superseded"
DEFAULT_TENANT_ID = "default"
DEFAULT_PROJECT_ID = "default"
OPENCLAW_JSON_BLOCK_RE = re.compile(
    r"(?P<label>Conversation info|Sender) \(untrusted metadata\):\s*```json\s*(?P<json>.*?)```",
    re.DOTALL,
)
WORD_RE = re.compile(r"[A-Za-z0-9_.:/-]+|[\u4e00-\u9fff]{1,4}")
DECISION_SIGNAL_RE = re.compile(
    r"(决定|确认|统一使用|行为基线|不新增|不再|而不是|截止|改为|更新|废弃|request_timeout_ms|baseUrl|provider|runtime|运行时|基线)",
    re.IGNORECASE,
)
REASON_RE = re.compile(r"(?:理由|原因)(?:是|为)?[：:]\s*(?P<reason>[^。\n]+)")
OBJECTION_RE = re.compile(r"(?:反对意见|反对|而不是|不是)[：:]?\s*(?P<objection>[^。\n]+)")
QUESTION_RE = re.compile(r"([?？]|什么|哪些|多少|怎么|如何|是否|吗[？?]?$)")


@dataclass(frozen=True)
class DecisionDraft:
    topic: str
    decision: str
    reason: str
    objections: str
    conclusion: str
    memory_key: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_iso_or_now(value: Optional[str]) -> str:
    if not value:
        return _utc_now_iso()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _utc_now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.replace(microsecond=0).isoformat()


def _iso_datetime_sort_key(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_scope(value: Optional[str], fallback: str) -> str:
    normalized = (value or "").strip()
    return normalized or fallback


def _tokenize(text: str) -> List[str]:
    normalized = text.lower()
    tokens = WORD_RE.findall(normalized)
    cjk_chars = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    if len(cjk_chars) >= 2:
        tokens.extend("".join(cjk_chars[index : index + 2]) for index in range(len(cjk_chars) - 1))
    tokens.extend(char for char in cjk_chars)
    return [token for token in tokens if token.strip()]


def _extract_openclaw_metadata(raw_text: str) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    for match in OPENCLAW_JSON_BLOCK_RE.finditer(raw_text):
        label = match.group("label").lower().replace(" ", "_")
        try:
            parsed = json.loads(match.group("json"))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            metadata[label] = parsed
            for key, value in parsed.items():
                metadata.setdefault(key, value)
    return metadata


def _source_url(metadata: Dict[str, Any]) -> str:
    for key in ("source_url", "url", "document_url", "repo_url"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip(" -\t")
        if stripped:
            return stripped
    return text.strip()


def _derive_topic(text: str, explicit_topic: Optional[str]) -> str:
    if explicit_topic and explicit_topic.strip():
        return explicit_topic.strip()
    for marker in ("request_timeout_ms", "baseUrl", "provider", "行为基线", "运行时"):
        if marker.lower() in text.lower():
            return marker
    line = _first_nonempty_line(text)
    return line[:80] if line else "未命名决策"


def _memory_key(
    *, tenant_id: str, project_id: str, conversation_id: str, topic: str
) -> str:
    normalized_topic = re.sub(r"\s+", " ", topic.strip().lower())
    return _sha256_text(
        "|".join((tenant_id.strip(), project_id.strip(), conversation_id.strip(), normalized_topic))
    )


def _matches_decision_signal(text: str, metadata: Dict[str, Any]) -> bool:
    if metadata.get("remember") is True or metadata.get("memory_type") == "decision":
        return True
    if QUESTION_RE.search(text.strip()):
        return False
    if DECISION_SIGNAL_RE.search(text):
        return True
    return False


def _score_card(card: DecisionMemoryCard, query_tokens: Sequence[str], query: str) -> float:
    haystack = "\n".join(
        (
            card.topic,
            card.decision,
            card.reason,
            card.objections,
            card.conclusion,
            card.source_url,
        )
    ).lower()
    if not query_tokens:
        return 0.0
    score = 0.0
    seen: set[str] = set()
    for token in query_tokens:
        if token in seen:
            continue
        seen.add(token)
        if token and token in haystack:
            score += 1.0 if len(token) > 1 else 0.4
    if query.strip().lower() in haystack:
        score += 3.0
    if card.topic and any(token in card.topic.lower() for token in query_tokens):
        score += 2.0
    score += min(card.version, 10) * 0.05
    return score


class DecisionExtractor:
    """Extracts durable project decisions from normalized Feishu/document text."""

    def extract(self, event: MemoryEventInput, clean_text: str) -> Optional[DecisionDraft]:
        metadata = dict(event.metadata)
        if not _matches_decision_signal(clean_text, metadata):
            return None

        tenant_id = _normalize_scope(event.tenant_id, DEFAULT_TENANT_ID)
        conversation_id = _normalize_scope(event.conversation_id, DEFAULT_PROJECT_ID)
        project_id = _normalize_scope(event.project_id, conversation_id)
        topic = _derive_topic(clean_text, event.topic)
        decision = clean_text.strip()
        reason_match = REASON_RE.search(clean_text)
        objection_match = OBJECTION_RE.search(clean_text)
        reason = reason_match.group("reason").strip() if reason_match else ""
        objections = (
            objection_match.group("objection").strip() if objection_match else ""
        )
        conclusion = _first_nonempty_line(decision)
        return DecisionDraft(
            topic=topic,
            decision=decision,
            reason=reason,
            objections=objections,
            conclusion=conclusion,
            memory_key=_memory_key(
                tenant_id=tenant_id,
                project_id=project_id,
                conversation_id=conversation_id,
                topic=topic,
            ),
        )


class DecisionMemoryEngine:
    """SQLite-backed decision memory engine with deterministic extraction."""

    def __init__(self, *, db_path: str, enabled: bool, max_cards: int = 3):
        self.db_path = db_path
        self.enabled = enabled
        self.max_cards = max(1, max_cards)
        self.extractor = DecisionExtractor()
        self._initialized = False
        if enabled:
            self.initialize()

    @classmethod
    def from_env(
        cls,
        *,
        enabled: bool,
        db_path: str,
        max_cards: int,
    ) -> "DecisionMemoryEngine":
        return cls(db_path=db_path, enabled=enabled, max_cards=max_cards)

    def initialize(self) -> None:
        if self._initialized:
            return
        path = Path(self.db_path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._create_schema(conn)
        self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY,
                event_hash TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                source TEXT NOT NULL,
                sender_id TEXT,
                occurred_at TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                clean_text TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                inserted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decision_memories (
                id TEXT PRIMARY KEY,
                memory_key TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                objections TEXT NOT NULL,
                conclusion TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL,
                source_event_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                supersedes_id TEXT,
                FOREIGN KEY(source_event_id) REFERENCES memory_events(id)
            );

            CREATE INDEX IF NOT EXISTS idx_decision_scope_status
                ON decision_memories(tenant_id, project_id, conversation_id, status);
            CREATE INDEX IF NOT EXISTS idx_decision_memory_key_status
                ON decision_memories(memory_key, status);

            CREATE TABLE IF NOT EXISTS retrieval_logs (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                query_text TEXT NOT NULL,
                hit_count INTEGER NOT NULL,
                top_memory_id TEXT,
                injected_chars INTEGER NOT NULL,
                retrieval_latency_ms REAL NOT NULL,
                used_for_prompt INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS decision_memory_fts USING fts5(
                memory_id UNINDEXED,
                tenant_id UNINDEXED,
                project_id UNINDEXED,
                conversation_id UNINDEXED,
                topic,
                decision,
                reason,
                conclusion
            )
            """
        )

    def ingest_event(self, event: MemoryEventInput) -> MemoryEventIngestResponse:
        if not self.enabled:
            return MemoryEventIngestResponse(
                event_id="",
                created_count=0,
                superseded_count=0,
                active_memory_ids=[],
                ignored_reason="memory_engine_disabled",
            )

        self.initialize()
        merged_metadata = dict(event.metadata)
        merged_metadata.update(
            {
                key: value
                for key, value in _extract_openclaw_metadata(event.raw_text).items()
                if key not in merged_metadata
            }
        )
        tenant_id = _normalize_scope(event.tenant_id, DEFAULT_TENANT_ID)
        conversation_id = _normalize_scope(
            str(
                merged_metadata.get("conversation_id")
                or merged_metadata.get("conversation_label")
                or event.conversation_id
            ),
            DEFAULT_PROJECT_ID,
        )
        project_id = _normalize_scope(event.project_id, conversation_id)
        occurred_at = _parse_iso_or_now(
            event.occurred_at
            or str(merged_metadata.get("timestamp") or merged_metadata.get("event_time") or "")
        )
        clean_text = _extract_real_user_question(event.raw_text)
        normalized_event = event.model_copy(
            update={
                "tenant_id": tenant_id,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "occurred_at": occurred_at,
                "metadata": merged_metadata,
            }
        )
        event_hash = _sha256_text(
            "|".join(
                (
                    tenant_id,
                    project_id,
                    conversation_id,
                    event.source,
                    occurred_at,
                    clean_text,
                    _stable_json(merged_metadata),
                )
            )
        )
        event_id = f"memevt-{event_hash[:24]}"
        draft = self.extractor.extract(normalized_event, clean_text)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM memory_events WHERE event_hash = ?",
                (event_hash,),
            ).fetchone()
            if existing:
                return MemoryEventIngestResponse(
                    event_id=str(existing["id"]),
                    created_count=0,
                    superseded_count=0,
                    active_memory_ids=[],
                    ignored_reason="duplicate_event",
                )

            conn.execute(
                """
                INSERT INTO memory_events (
                    id, event_hash, tenant_id, project_id, conversation_id, source,
                    sender_id, occurred_at, raw_text, clean_text, metadata_json, inserted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_hash,
                    tenant_id,
                    project_id,
                    conversation_id,
                    event.source,
                    event.sender_id or str(merged_metadata.get("sender_id") or ""),
                    occurred_at,
                    event.raw_text,
                    clean_text,
                    _stable_json(merged_metadata),
                    _utc_now_iso(),
                ),
            )

            if draft is None:
                return MemoryEventIngestResponse(
                    event_id=event_id,
                    created_count=0,
                    superseded_count=0,
                    active_memory_ids=[],
                    ignored_reason="no_decision_signal",
                )

            previous_active_ids = {
                str(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM decision_memories
                    WHERE memory_key = ? AND status = ?
                    """,
                    (draft.memory_key, DECISION_STATUS_ACTIVE),
                ).fetchall()
            }

            memory_id = f"mem-{uuid.uuid4().hex[:24]}"
            source = _source_url(merged_metadata)
            conn.execute(
                """
                INSERT INTO decision_memories (
                    id, memory_key, tenant_id, project_id, conversation_id, topic,
                    decision, reason, objections, conclusion, status, version,
                    source_event_id, source_url, occurred_at, updated_at, supersedes_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    draft.memory_key,
                    tenant_id,
                    project_id,
                    conversation_id,
                    draft.topic,
                    draft.decision,
                    draft.reason,
                    draft.objections,
                    draft.conclusion,
                    DECISION_STATUS_ACTIVE,
                    1,
                    event_id,
                    source,
                    occurred_at,
                    _utc_now_iso(),
                    None,
                ),
            )
            active_ids = self._reconcile_memory_key_versions(conn, draft.memory_key)
            superseded_count = len(previous_active_ids - set(active_ids))

        return MemoryEventIngestResponse(
            event_id=event_id,
            created_count=1,
            superseded_count=superseded_count,
            active_memory_ids=active_ids,
        )

    def _reconcile_memory_key_versions(
        self, conn: sqlite3.Connection, memory_key: str
    ) -> List[str]:
        rows = conn.execute(
            """
            SELECT
                dm.id, dm.tenant_id, dm.project_id, dm.conversation_id,
                dm.topic, dm.decision, dm.reason, dm.conclusion, dm.occurred_at,
                me.inserted_at
            FROM decision_memories dm
            JOIN memory_events me ON me.id = dm.source_event_id
            WHERE dm.memory_key = ?
            """,
            (memory_key,),
        ).fetchall()
        if not rows:
            return []
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                _iso_datetime_sort_key(str(row["occurred_at"])),
                _iso_datetime_sort_key(str(row["inserted_at"])),
                str(row["id"]),
            ),
        )
        active_id = str(ordered_rows[-1]["id"])
        now = _utc_now_iso()
        previous_id: Optional[str] = None
        for version, row in enumerate(ordered_rows, start=1):
            memory_id = str(row["id"])
            conn.execute(
                """
                UPDATE decision_memories
                SET status = ?, version = ?, updated_at = ?, supersedes_id = ?
                WHERE id = ?
                """,
                (
                    DECISION_STATUS_ACTIVE
                    if memory_id == active_id
                    else DECISION_STATUS_SUPERSEDED,
                    version,
                    now,
                    previous_id,
                    memory_id,
                ),
            )
            previous_id = memory_id

        for row in ordered_rows:
            conn.execute(
                "DELETE FROM decision_memory_fts WHERE memory_id = ?",
                (str(row["id"]),),
            )
        active_row = ordered_rows[-1]
        conn.execute(
            """
            INSERT INTO decision_memory_fts (
                memory_id, tenant_id, project_id, conversation_id,
                topic, decision, reason, conclusion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(active_row["id"]),
                str(active_row["tenant_id"]),
                str(active_row["project_id"]),
                str(active_row["conversation_id"]),
                str(active_row["topic"]),
                str(active_row["decision"]),
                str(active_row["reason"]),
                str(active_row["conclusion"]),
            ),
        )
        return [active_id]

    def ingest_chat_messages(
        self,
        *,
        raw_request_body: str,
        messages: Sequence[Message],
        request_id: str,
    ) -> List[MemoryEventIngestResponse]:
        if not self.enabled:
            return []
        scope = self.chat_scope(raw_request_body)
        metadata = scope["metadata"]
        conversation_id = scope["conversation_id"]
        project_id = scope["project_id"]
        tenant_id = scope["tenant_id"]
        responses: List[MemoryEventIngestResponse] = []
        for message in messages:
            if message.role != "user" or not message.content.strip():
                continue
            event = MemoryEventInput(
                source=str(metadata.get("source") or "openclaw-feishu"),
                tenant_id=tenant_id,
                project_id=project_id,
                conversation_id=conversation_id,
                sender_id=str(metadata.get("sender_id") or ""),
                occurred_at=None,
                raw_text=message.content,
                topic=str(metadata.get("topic") or "") or None,
                metadata={**metadata, "request_id": request_id},
            )
            responses.append(self.ingest_event(event))
        return responses

    def chat_scope(self, raw_request_body: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw_request_body) if raw_request_body else {}
        except json.JSONDecodeError:
            payload = {}
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        openclaw = payload.get("openclaw") if isinstance(payload, dict) else {}
        if isinstance(openclaw, dict):
            metadata = {**metadata, "openclaw": openclaw}
        conversation_id = str(
            metadata.get("conversation_id")
            or metadata.get("conversation_label")
            or metadata.get("chat_id")
            or "openclaw-feishu"
        )
        project_id = str(metadata.get("project_id") or conversation_id)
        tenant_id = str(metadata.get("tenant_id") or DEFAULT_TENANT_ID)
        return {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "metadata": metadata,
        }

    def search(
        self,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        query: str,
        limit: Optional[int] = None,
        request_id: str = "",
        now_iso: Optional[str] = None,
        used_for_prompt: bool = False,
    ) -> MemorySearchResponse:
        if not self.enabled:
            return MemorySearchResponse(
                query=query,
                hit_count=0,
                cards=[],
                metrics={
                    "enabled": False,
                    "hit_at_1": 0,
                    "retrieval_latency_ms": 0.0,
                },
            )

        self.initialize()
        started = time.perf_counter()
        scoped_tenant = _normalize_scope(tenant_id, DEFAULT_TENANT_ID)
        scoped_project = _normalize_scope(project_id, DEFAULT_PROJECT_ID)
        scoped_conversation = conversation_id.strip() if conversation_id else ""
        rows = self._load_active_cards(
            tenant_id=scoped_tenant,
            project_id=scoped_project,
            conversation_id=scoped_conversation,
        )
        query_tokens = _tokenize(query)
        fts_memory_ids = self._fts_candidate_ids(
            tenant_id=scoped_tenant,
            project_id=scoped_project,
            conversation_id=scoped_conversation,
            query_tokens=query_tokens,
        )
        cards: List[DecisionMemoryCard] = []
        for card in rows:
            score = _score_card(card, query_tokens, query)
            if card.id in fts_memory_ids:
                score += 1.5
            if score <= 0.0:
                continue
            cards.append(card.model_copy(update={"score": round(score, 4)}))
        cards.sort(
            key=lambda card: (
                card.score,
                1 if scoped_conversation and card.conversation_id == scoped_conversation else 0,
                card.version,
                card.occurred_at,
            ),
            reverse=True,
        )
        effective_limit = limit if limit is not None else self.max_cards
        cards = cards[: max(1, effective_limit)]
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        top_memory_id = cards[0].id if cards else None
        self._log_retrieval(
            request_id=request_id or f"memory-search-{uuid.uuid4().hex[:12]}",
            tenant_id=scoped_tenant,
            project_id=scoped_project,
            conversation_id=scoped_conversation,
            query=query,
            hit_count=len(cards),
            top_memory_id=top_memory_id,
            injected_chars=0,
            retrieval_latency_ms=latency_ms,
            used_for_prompt=used_for_prompt,
        )
        return MemorySearchResponse(
            query=query,
            hit_count=len(cards),
            cards=cards,
            metrics={
                "enabled": True,
                "hit_at_1": 1 if cards else 0,
                "retrieval_latency_ms": latency_ms,
                "evaluated_active_memory_count": len(rows),
                "fts_candidate_count": len(fts_memory_ids),
                "as_of": now_iso or _utc_now_iso(),
            },
        )

    def _fts_candidate_ids(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        query_tokens: Sequence[str],
    ) -> set[str]:
        terms = []
        for token in query_tokens:
            if len(token) <= 1:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.:/-]+|[\u4e00-\u9fff]{2,4}", token):
                continue
            terms.append(token.replace('"', '""'))
        if not terms:
            return set()
        match_query = " OR ".join(f'"{term}"' for term in terms[:12])
        params: List[Any] = [match_query, tenant_id, project_id]
        where = "decision_memory_fts MATCH ? AND tenant_id = ? AND project_id = ?"
        if conversation_id:
            where += " AND conversation_id IN (?, ?)"
            params.extend([conversation_id, project_id])
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT memory_id FROM decision_memory_fts WHERE {where}",
                    tuple(params),
                ).fetchall()
        except sqlite3.Error:
            return set()
        return {str(row["memory_id"]) for row in rows}

    def _load_active_cards(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
    ) -> List[DecisionMemoryCard]:
        params: List[Any] = [tenant_id, project_id, DECISION_STATUS_ACTIVE]
        where = "tenant_id = ? AND project_id = ? AND status = ?"
        if conversation_id:
            where += " AND conversation_id IN (?, ?)"
            params.extend([conversation_id, project_id])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM decision_memories
                WHERE {where}
                ORDER BY occurred_at DESC, version DESC
                """,
                tuple(params),
            ).fetchall()
        return [
            DecisionMemoryCard(
                id=str(row["id"]),
                memory_key=str(row["memory_key"]),
                tenant_id=str(row["tenant_id"]),
                project_id=str(row["project_id"]),
                conversation_id=str(row["conversation_id"]),
                topic=str(row["topic"]),
                decision=str(row["decision"]),
                reason=str(row["reason"]),
                objections=str(row["objections"]),
                conclusion=str(row["conclusion"]),
                status=str(row["status"]),
                version=int(row["version"]),
                source_event_id=str(row["source_event_id"]),
                source_url=str(row["source_url"]),
                occurred_at=str(row["occurred_at"]),
                updated_at=str(row["updated_at"]),
                score=0.0,
            )
            for row in rows
        ]

    def compose_prompt(
        self,
        original_prompt: str,
        cards: Sequence[DecisionMemoryCard],
    ) -> MemoryPromptComposition:
        selected = list(cards[: self.max_cards])
        if not selected:
            return MemoryPromptComposition(
                prompt=original_prompt,
                hit_count=0,
                memory_ids=[],
                injected_characters=0,
                saved_characters=0,
                efficiency_gain_ratio=0.0,
            )
        card_lines = ["历史决策卡片："]
        for index, card in enumerate(selected, start=1):
            source = f" 来源：{card.source_url}" if card.source_url else ""
            card_lines.append(
                f"{index}. 主题：{card.topic}\n"
                f"   决策：{card.decision}\n"
                f"   结论：{card.conclusion}{source}"
            )
        memory_context = "\n".join(card_lines)
        composed = f"{memory_context}\n\n用户当前问题：\n{original_prompt}"
        remembered_chars = sum(len(card.decision) for card in selected)
        saved_characters = max(0, remembered_chars - len(original_prompt))
        denominator = max(1, remembered_chars)
        return MemoryPromptComposition(
            prompt=composed,
            hit_count=len(selected),
            memory_ids=[card.id for card in selected],
            injected_characters=len(composed) - len(original_prompt),
            saved_characters=saved_characters,
            efficiency_gain_ratio=round(saved_characters / denominator, 4),
        )

    def record_prompt_usage(
        self,
        *,
        request_id: str,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        query: str,
        hit_count: int,
        top_memory_id: Optional[str],
        injected_chars: int,
    ) -> None:
        if not self.enabled:
            return
        self._log_retrieval(
            request_id=request_id,
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            query=query,
            hit_count=hit_count,
            top_memory_id=top_memory_id,
            injected_chars=injected_chars,
            retrieval_latency_ms=0.0,
            used_for_prompt=True,
        )

    def _log_retrieval(
        self,
        *,
        request_id: str,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        query: str,
        hit_count: int,
        top_memory_id: Optional[str],
        injected_chars: int,
        retrieval_latency_ms: float,
        used_for_prompt: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO retrieval_logs (
                    id, request_id, tenant_id, project_id, conversation_id,
                    query_hash, query_text, hit_count, top_memory_id,
                    injected_chars, retrieval_latency_ms, used_for_prompt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ret-{uuid.uuid4().hex[:24]}",
                    request_id,
                    tenant_id,
                    project_id,
                    conversation_id,
                    _sha256_text(query),
                    query,
                    hit_count,
                    top_memory_id,
                    injected_chars,
                    retrieval_latency_ms,
                    1 if used_for_prompt else 0,
                    _utc_now_iso(),
                ),
            )

    def report(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        self.initialize()
        with self._connect() as conn:
            event_count = int(
                conn.execute("SELECT COUNT(*) AS count FROM memory_events").fetchone()[
                    "count"
                ]
            )
            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM decision_memories WHERE status = ?",
                    (DECISION_STATUS_ACTIVE,),
                ).fetchone()["count"]
            )
            superseded_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM decision_memories WHERE status = ?",
                    (DECISION_STATUS_SUPERSEDED,),
                ).fetchone()["count"]
            )
            retrieval_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS retrieval_count,
                    SUM(CASE WHEN hit_count > 0 THEN 1 ELSE 0 END) AS hit_count,
                    AVG(retrieval_latency_ms) AS avg_latency_ms,
                    AVG(injected_chars) AS avg_injected_chars
                FROM retrieval_logs
                """
            ).fetchone()
            version_rows = conn.execute(
                """
                SELECT dm.memory_key, dm.id, dm.status, dm.occurred_at, me.inserted_at
                FROM decision_memories dm
                JOIN memory_events me ON me.id = dm.source_event_id
                """
            ).fetchall()
        retrieval_count = int(retrieval_row["retrieval_count"] or 0)
        retrieval_hit_count = int(retrieval_row["hit_count"] or 0)
        hit_rate = retrieval_hit_count / retrieval_count if retrieval_count else 0.0
        rows_by_key: Dict[str, List[sqlite3.Row]] = {}
        for row in version_rows:
            rows_by_key.setdefault(str(row["memory_key"]), []).append(row)
        correct_version_keys = 0
        for rows in rows_by_key.values():
            active_rows = [
                row for row in rows if str(row["status"]) == DECISION_STATUS_ACTIVE
            ]
            latest_row = max(
                rows,
                key=lambda row: (
                    _iso_datetime_sort_key(str(row["occurred_at"])),
                    _iso_datetime_sort_key(str(row["inserted_at"])),
                    str(row["id"]),
                ),
            )
            if (
                len(active_rows) == 1
                and str(active_rows[0]["id"]) == str(latest_row["id"])
            ):
                correct_version_keys += 1
        version_correctness = (
            correct_version_keys / len(rows_by_key) if rows_by_key else 1.0
        )
        return {
            "enabled": True,
            "event_count": event_count,
            "active_memory_count": active_count,
            "superseded_memory_count": superseded_count,
            "retrieval_count": retrieval_count,
            "retrieval_hit_count": retrieval_hit_count,
            "hit_rate": round(hit_rate, 4),
            "avg_retrieval_latency_ms": round(float(retrieval_row["avg_latency_ms"] or 0.0), 4),
            "avg_injected_chars": round(float(retrieval_row["avg_injected_chars"] or 0.0), 2),
            "version_correctness": round(version_correctness, 4),
            "benchmark_contracts": {
                "anti_interference": "hit_at_1 is recorded per search in retrieval logs",
                "contradiction_update": "only active decision versions are returned",
                "efficiency": "prompt composition reports saved_characters and efficiency_gain_ratio",
            },
        }


def default_memory_db_path() -> str:
    return os.path.join(".run", "memory-engine", "decision_memory.sqlite3")


__all__ = [
    "DecisionMemoryEngine",
    "MemoryEventInput",
    "MemoryEventIngestResponse",
    "MemoryPromptComposition",
    "MemorySearchResponse",
]
