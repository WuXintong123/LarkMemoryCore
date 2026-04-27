import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
import pytest

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_test_dir = os.path.abspath(os.path.dirname(__file__))
if _test_dir not in sys.path:
    sys.path.insert(0, _test_dir)

from openclaw_feishu_cases import build_real_openclaw_feishu_transport_text
from api_server.auth import ApiKeyAuthManager


REPO_ROOT = Path(_project_root)


def _real_runbook_first_decision() -> str:
    text = (REPO_ROOT / "docs/openclaw-feishu-runbook.md").read_text(encoding="utf-8")
    start = text.index("竞赛交付版本统一使用")
    end = text.index("本次唯一行为基线是：")
    return text[start:end].strip()


def _real_runbook_baseline_decision() -> str:
    text = (REPO_ROOT / "docs/openclaw-feishu-runbook.md").read_text(encoding="utf-8")
    start = text.index("本次唯一行为基线是：")
    end = text.index("## 2. 联调前准备")
    return text[start:end].strip()


def _real_runtime_timeout_decision() -> str:
    text = (REPO_ROOT / "ops/feishu_office_competition_common.sh").read_text(
        encoding="utf-8"
    )
    needle = '"request_timeout_ms": 300000'
    start = text.index(needle)
    return "竞赛运行时模型配置更新：" + text[start : start + len(needle)].strip()


def _real_runbook_timeout_decision() -> str:
    text = (REPO_ROOT / "docs/openclaw-feishu-runbook.md").read_text(encoding="utf-8")
    needle = "- `request_timeout_ms = 30000`"
    start = text.index(needle)
    return "确认真实模型 serving 配置：" + text[start : start + len(needle)].strip()


def _real_dataset_noise(limit: int = 40):
    rows = []
    path = REPO_ROOT / "competition/feishu_office/data/test.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
        if len(rows) >= limit:
            break
    return rows


def _disabled_auth_manager() -> ApiKeyAuthManager:
    return ApiKeyAuthManager.from_config(
        legacy_api_key="",
        legacy_key_id="default",
        legacy_scopes="models:read,inference,admin",
        legacy_allowed_models="",
        api_keys_file="",
        api_keys_json="",
    )


def _policy_record(**policy_overrides):
    policy = {
        "api_mode": "both",
        "prompt_style": "buddy_deepseek_r1",
        "default_max_tokens": 32,
        "max_max_tokens": 512,
        "max_input_chars": 12000,
        "request_timeout_ms": 120000,
        "stream_idle_timeout_s": 15,
        "allow_anonymous_models": False,
    }
    policy.update(policy_overrides)
    return {
        "id": "test-model",
        "object": "model",
        "created": 1,
        "owned_by": "ruyi",
        "_serving_policy": policy,
    }


def _completed_result(output: str, request_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        output=output,
        request_id=request_id,
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
        completion_status="completed",
        completion_detail="",
    )


def test_decision_memory_survives_real_dataset_interference(tmp_path):
    from api_server.services.memory_service import DecisionMemoryEngine, MemoryEventInput

    engine = DecisionMemoryEngine(
        db_path=str(tmp_path / "memory.sqlite3"),
        enabled=True,
        max_cards=3,
    )
    decision_text = _real_runbook_first_decision()
    wrapped = build_real_openclaw_feishu_transport_text(
        decision_text,
        message_id="om_real_runbook_decision",
        surface_label="Feishu[default] Group",
    )
    ingest = engine.ingest_event(
        MemoryEventInput(
            source="openclaw-feishu",
            tenant_id="tenant-real",
            project_id="feishu-office",
            conversation_id="oc_group_trace_room",
            sender_id="ou_b7a2af6fd238fe904886425f8477efe5",
            occurred_at="2026-04-18T10:00:00+08:00",
            raw_text=wrapped,
            topic="竞赛运行时基线",
            metadata={"source_url": "repo://docs/openclaw-feishu-runbook.md"},
        )
    )
    assert ingest.created_count == 1

    for index, row in enumerate(_real_dataset_noise()):
        engine.ingest_event(
            MemoryEventInput(
                source="document",
                tenant_id="tenant-real",
                project_id="feishu-office",
                conversation_id=f"noise-{index}",
                occurred_at="2026-04-19T10:00:00+08:00",
                raw_text=row["input"],
                topic=row["source_title"],
                metadata={"source_url": row["source_url"], "task": row["task"]},
            )
        )

    results = engine.search(
        tenant_id="tenant-real",
        project_id="feishu-office",
        conversation_id="oc_group_trace_room",
        query="竞赛运行时不用 legacy systemd 时应该使用哪些脚本？",
        limit=3,
        request_id="req-memory-interference",
        now_iso="2026-04-25T10:00:00+08:00",
    )

    assert results.hit_count >= 1
    assert results.cards[0].status == "active"
    assert results.cards[0].source_url == "repo://docs/openclaw-feishu-runbook.md"
    assert "feishu_office_competition_start.sh" in results.cards[0].decision
    assert "systemd --user" in results.cards[0].decision
    assert results.metrics["hit_at_1"] == 1


def test_decision_memory_keeps_latest_real_conflicting_runtime_value(tmp_path):
    from api_server.services.memory_service import DecisionMemoryEngine, MemoryEventInput

    engine = DecisionMemoryEngine(
        db_path=str(tmp_path / "memory.sqlite3"),
        enabled=True,
        max_cards=3,
    )
    first = engine.ingest_event(
        MemoryEventInput(
            source="document",
            tenant_id="tenant-real",
            project_id="feishu-office",
            conversation_id="oc_group_trace_room",
            occurred_at="2026-04-13T10:00:00+08:00",
            raw_text=_real_runbook_timeout_decision(),
            topic="request_timeout_ms",
            metadata={"source_url": "repo://docs/openclaw-feishu-runbook.md"},
        )
    )
    second = engine.ingest_event(
        MemoryEventInput(
            source="document",
            tenant_id="tenant-real",
            project_id="feishu-office",
            conversation_id="oc_group_trace_room",
            occurred_at="2026-04-18T10:00:00+08:00",
            raw_text=_real_runtime_timeout_decision(),
            topic="request_timeout_ms",
            metadata={"source_url": "repo://ops/feishu_office_competition_common.sh"},
        )
    )

    assert first.created_count == 1
    assert second.superseded_count == 1

    results = engine.search(
        tenant_id="tenant-real",
        project_id="feishu-office",
        conversation_id="oc_group_trace_room",
        query="竞赛运行时 request_timeout_ms 使用多少？",
        limit=5,
        request_id="req-memory-update",
    )

    assert results.hit_count == 1
    assert "300000" in results.cards[0].decision
    assert results.cards[0].source_url == "repo://ops/feishu_office_competition_common.sh"
    assert results.cards[0].version == 2
    report = engine.report()
    assert report["superseded_memory_count"] == 1
    assert report["active_memory_count"] == 1
    assert report["version_correctness"] == 1.0


def test_memory_context_composer_quantifies_efficiency(tmp_path):
    from api_server.services.memory_service import DecisionMemoryEngine, MemoryEventInput

    engine = DecisionMemoryEngine(
        db_path=str(tmp_path / "memory.sqlite3"),
        enabled=True,
        max_cards=3,
    )
    decision_text = _real_runbook_baseline_decision()
    engine.ingest_event(
        MemoryEventInput(
            source="document",
            tenant_id="tenant-real",
            project_id="feishu-office",
            conversation_id="oc_group_trace_room",
            occurred_at="2026-04-18T10:00:00+08:00",
            raw_text=decision_text,
            topic="OpenClaw Feishu 行为基线",
            metadata={"source_url": "repo://docs/openclaw-feishu-runbook.md"},
        )
    )

    short_query = "基线是什么？"
    results = engine.search(
        tenant_id="tenant-real",
        project_id="feishu-office",
        conversation_id="oc_group_trace_room",
        query=short_query,
        request_id="req-memory-efficiency",
    )
    composed = engine.compose_prompt(short_query, results.cards)

    assert "历史决策卡片" in composed.prompt
    assert "POST /v1/chat/completions" in composed.prompt
    assert composed.hit_count == 1
    assert composed.saved_characters > 0
    assert composed.efficiency_gain_ratio > 0.5


@pytest.mark.asyncio
async def test_memory_admin_routes_ingest_search_and_report(tmp_path):
    from api_server.main import app
    import api_server.main as main_module
    from api_server.services.memory_service import DecisionMemoryEngine

    original_engine = main_module.memory_engine
    main_module.memory_engine = DecisionMemoryEngine(
        db_path=str(tmp_path / "memory.sqlite3"),
        enabled=True,
        max_cards=3,
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/memory/events",
                json={
                    "source": "document",
                    "tenant_id": "tenant-real",
                    "project_id": "feishu-office",
                    "conversation_id": "oc_group_trace_room",
                    "occurred_at": "2026-04-18T10:00:00+08:00",
                    "raw_text": _real_runbook_first_decision(),
                    "topic": "竞赛运行时基线",
                    "metadata": {
                        "source_url": "repo://docs/openclaw-feishu-runbook.md"
                    },
                },
            )
            search_response = await client.get(
                "/v1/memory/search",
                params={
                    "tenant_id": "tenant-real",
                    "project_id": "feishu-office",
                    "conversation_id": "oc_group_trace_room",
                    "query": "竞赛运行时使用哪些脚本管理？",
                },
            )
            report_response = await client.get("/v1/memory/report")
    finally:
        main_module.memory_engine = original_engine

    assert ingest_response.status_code == 200
    assert ingest_response.json()["created_count"] == 1
    assert search_response.status_code == 200
    assert search_response.json()["hit_count"] == 1
    assert report_response.status_code == 200
    assert report_response.json()["active_memory_count"] == 1


@pytest.mark.asyncio
async def test_chat_route_injects_memory_without_storing_question_as_decision(tmp_path):
    from api_server.main import app
    import api_server.main as main_module
    from api_server.services.memory_service import DecisionMemoryEngine, MemoryEventInput

    engine = DecisionMemoryEngine(
        db_path=str(tmp_path / "memory.sqlite3"),
        enabled=True,
        max_cards=3,
    )
    engine.ingest_event(
        MemoryEventInput(
            source="document",
            tenant_id="tenant-real",
            project_id="feishu-office",
            conversation_id="oc_group_trace_room",
            occurred_at="2026-04-18T10:00:00+08:00",
            raw_text=_real_runbook_baseline_decision(),
            topic="OpenClaw Feishu 行为基线",
            metadata={"source_url": "repo://docs/openclaw-feishu-runbook.md"},
        )
    )

    captured_prompts = []

    def _process_with_stats(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return _completed_result("READY", "req-memory-chat")

    original_engine = main_module.memory_engine
    main_module.memory_engine = engine
    try:
        with patch("api_server.main.auth_manager", _disabled_auth_manager()), patch(
            "api_server.main._ensure_model_available"
        ), patch(
            "api_server.main._get_model_record", return_value=_policy_record()
        ), patch(
            "api_server.main.compute_client.process_with_stats",
            side_effect=_process_with_stats,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [
                            {"role": "user", "content": "之前确认的基线是什么？"}
                        ],
                        "max_tokens": 16,
                        "temperature": 0.0,
                        "metadata": {
                            "source": "openclaw-feishu",
                            "tenant_id": "tenant-real",
                            "project_id": "feishu-office",
                            "conversation_id": "oc_group_trace_room",
                        },
                    },
                )
    finally:
        main_module.memory_engine = original_engine

    assert response.status_code == 200
    assert response.headers["X-Ruyi-Memory-Hit-Count"] == "1"
    assert len(captured_prompts) == 1
    assert "历史决策卡片" in captured_prompts[0]
    assert "POST /v1/chat/completions" in captured_prompts[0]
    assert "之前确认的基线是什么？" not in captured_prompts[0].split("历史决策卡片：", 1)[1].split("用户当前问题：", 1)[0]
    report = engine.report()
    assert report["active_memory_count"] == 1
