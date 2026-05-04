import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
import pytest

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api_server.auth import ApiKeyAuthManager
from api_server.services.memory_service import DecisionMemoryEngine
from competition.feishu_office.seed_memory_engine import (
    DEMO_CONVERSATION_ID,
    DEMO_PROJECT_ID,
    DEMO_TENANT_ID,
    seed_engine,
)


def _disabled_auth_manager() -> ApiKeyAuthManager:
    return ApiKeyAuthManager.from_config(
        legacy_api_key="",
        legacy_key_id="default",
        legacy_scopes="models:read,inference,admin",
        legacy_allowed_models="",
        api_keys_file="",
        api_keys_json="",
    )


@pytest.mark.asyncio
async def test_competition_evidence_api_returns_real_metrics_without_model_outputs():
    from api_server.main import app

    with patch("api_server.main.auth_manager", _disabled_auth_manager()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/competition/feishu-office/evidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset"]["manifest"]["row_count"] >= 1200
    assert payload["dataset"]["quality_report"]["rows_by_split"]["train"] >= 1000
    assert payload["evaluation"]["baseline"]["success_count"] == 3
    assert payload["evaluation"]["tuned"]["success_count"] == 3
    assert all(item["passed"] for item in payload["feishu_acceptance"]["latest_passed"])
    assert {
        item["scenario"] for item in payload["feishu_acceptance"]["latest_passed"]
    } == {"dm-nonstream", "dm-stream", "group-at-nonstream", "group-at-stream"}

    serialized = json.dumps(payload, ensure_ascii=False)
    assert '"output"' not in serialized
    assert "嗯，用户给了" not in serialized
    assert "Ruyi Serving\\nRuyi Serving" not in serialized


def test_seed_engine_uses_real_files_and_is_repeatable(tmp_path):
    engine = DecisionMemoryEngine(
        db_path=str(tmp_path / "memory.sqlite3"),
        enabled=True,
        max_cards=3,
    )

    first = seed_engine(engine)
    second = seed_engine(engine)

    assert first["created_count"] >= 3
    assert first["noise_event_count"] >= 40
    assert second["duplicate_count"] >= first["created_count"]
    assert second["created_count"] == 0

    runtime_results = engine.search(
        tenant_id=DEMO_TENANT_ID,
        project_id=DEMO_PROJECT_ID,
        conversation_id=DEMO_CONVERSATION_ID,
        query="竞赛运行时 request_timeout_ms 使用多少？",
        limit=3,
        request_id="req-seed-runtime-timeout",
    )
    assert runtime_results.hit_count >= 1
    assert "300000" in runtime_results.cards[0].decision
    assert runtime_results.cards[0].status == "active"
    assert runtime_results.cards[0].version == 2

    interference_results = engine.search(
        tenant_id=DEMO_TENANT_ID,
        project_id=DEMO_PROJECT_ID,
        conversation_id=DEMO_CONVERSATION_ID,
        query="竞赛运行时不用 legacy systemd 时应该使用哪些脚本？",
        limit=3,
        request_id="req-seed-interference",
    )
    assert interference_results.hit_count >= 1
    assert "feishu_office_competition_start.sh" in interference_results.cards[0].decision

    report = engine.report()
    assert report["version_correctness"] == 1.0
