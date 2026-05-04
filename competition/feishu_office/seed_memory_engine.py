"""Seed the production memory engine with real Feishu Office delivery evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from api_server.schemas.memory import MemoryEventInput
from api_server.services.memory_service import DecisionMemoryEngine


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_TENANT_ID = "tenant-real"
DEMO_PROJECT_ID = "feishu-office"
DEMO_CONVERSATION_ID = "oc_group_trace_room"
DEMO_SENDER_ID = "ou_b7a2af6fd238fe904886425f8477efe5"


def _read_text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _runbook_first_decision() -> str:
    text = _read_text("docs/openclaw-feishu-runbook.md")
    start = text.index("竞赛交付版本统一使用")
    end = text.index("本次唯一行为基线是：")
    return text[start:end].strip()


def _runbook_baseline_decision() -> str:
    text = _read_text("docs/openclaw-feishu-runbook.md")
    start = text.index("本次唯一行为基线是：")
    end = text.index("## 2. 联调前准备")
    return text[start:end].strip()


def _runtime_timeout_decision() -> str:
    text = _read_text("ops/feishu_office_competition_common.sh")
    needle = '"request_timeout_ms": 300000'
    start = text.index(needle)
    return "竞赛运行时模型配置更新：" + text[start : start + len(needle)].strip()


def _runbook_timeout_decision() -> str:
    text = _read_text("docs/openclaw-feishu-runbook.md")
    needle = "- `request_timeout_ms = 30000`"
    start = text.index(needle)
    return "确认真实模型 serving 配置：" + text[start : start + len(needle)].strip()


def _real_dataset_noise(limit: int) -> Iterable[Dict[str, Any]]:
    path = REPO_ROOT / "competition" / "feishu_office" / "data" / "test.jsonl"
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        yield json.loads(line)
        count += 1
        if count >= limit:
            return


def _event(
    *,
    raw_text: str,
    topic: str,
    source_url: str,
    occurred_at: str,
    source: str = "document",
    conversation_id: str = DEMO_CONVERSATION_ID,
    metadata: Optional[Dict[str, Any]] = None,
) -> MemoryEventInput:
    return MemoryEventInput(
        source=source,
        tenant_id=DEMO_TENANT_ID,
        project_id=DEMO_PROJECT_ID,
        conversation_id=conversation_id,
        sender_id=DEMO_SENDER_ID,
        occurred_at=occurred_at,
        raw_text=raw_text,
        topic=topic,
        metadata={"source_url": source_url, **(metadata or {})},
    )


def build_seed_events(noise_limit: int = 40) -> List[MemoryEventInput]:
    events = [
        _event(
            raw_text=_runbook_first_decision(),
            topic="竞赛运行时基线",
            source_url="repo://docs/openclaw-feishu-runbook.md",
            occurred_at="2026-04-18T10:00:00+08:00",
        ),
        _event(
            raw_text=_runbook_baseline_decision(),
            topic="OpenClaw Feishu 行为基线",
            source_url="repo://docs/openclaw-feishu-runbook.md",
            occurred_at="2026-04-18T10:05:00+08:00",
        ),
        _event(
            raw_text=_runbook_timeout_decision(),
            topic="request_timeout_ms",
            source_url="repo://docs/openclaw-feishu-runbook.md",
            occurred_at="2026-04-13T10:00:00+08:00",
        ),
        _event(
            raw_text=_runtime_timeout_decision(),
            topic="request_timeout_ms",
            source_url="repo://ops/feishu_office_competition_common.sh",
            occurred_at="2026-04-18T10:00:00+08:00",
        ),
    ]
    for index, row in enumerate(_real_dataset_noise(noise_limit)):
        events.append(
            _event(
                raw_text=str(row["input"]),
                topic=str(row["source_title"]),
                source_url=str(row["source_url"]),
                occurred_at="2026-04-19T10:00:00+08:00",
                source="document",
                conversation_id=f"noise-{index}",
                metadata={"task": row.get("task", ""), "noise": True},
            )
        )
    return events


def _summary_from_responses(responses: Iterable[Any], noise_event_count: int) -> Dict[str, int]:
    created_count = 0
    duplicate_count = 0
    superseded_count = 0
    ignored_count = 0
    for response in responses:
        created_count += int(getattr(response, "created_count", 0))
        superseded_count += int(getattr(response, "superseded_count", 0))
        ignored_reason = getattr(response, "ignored_reason", None)
        if ignored_reason == "duplicate_event":
            duplicate_count += 1
        elif ignored_reason:
            ignored_count += 1
    return {
        "created_count": created_count,
        "duplicate_count": duplicate_count,
        "superseded_count": superseded_count,
        "ignored_count": ignored_count,
        "noise_event_count": noise_event_count,
    }


def seed_engine(engine: DecisionMemoryEngine, *, noise_limit: int = 40) -> Dict[str, int]:
    events = build_seed_events(noise_limit=noise_limit)
    responses = [engine.ingest_event(event) for event in events]
    return _summary_from_responses(responses, noise_event_count=noise_limit)


def seed_remote(
    *,
    base_url: str,
    api_key: str,
    noise_limit: int = 40,
    timeout_s: float = 30.0,
) -> Dict[str, int]:
    events = build_seed_events(noise_limit=noise_limit)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    responses = []
    for event in events:
        response = requests.post(
            f"{base_url.rstrip('/')}/v1/memory/events",
            headers=headers,
            json=event.model_dump(),
            timeout=timeout_s,
        )
        response.raise_for_status()
        responses.append(type("RemoteResponse", (), response.json()))
    return _summary_from_responses(responses, noise_event_count=noise_limit)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed LarkMemoryCore decision memory with real Feishu Office evidence."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18100")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--noise-limit", type=int, default=40)
    args = parser.parse_args()
    summary = seed_remote(
        base_url=args.base_url,
        api_key=args.api_key,
        noise_limit=args.noise_limit,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
