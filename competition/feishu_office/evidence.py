"""Evidence aggregation for the Feishu Office competition delivery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPO_ROOT = Path(__file__).resolve().parents[2]
COMP_ROOT = REPO_ROOT / "competition" / "feishu_office"
DATA_ROOT = COMP_ROOT / "data"
EVAL_ROOT = COMP_ROOT / "artifacts" / "eval"
REPORTS_ROOT = REPO_ROOT / "reports"


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sanitize_model_evaluation(value: Dict[str, Any]) -> Dict[str, Any]:
    allowed_keys = (
        "model_id",
        "sample_count",
        "success_count",
        "failure_count",
        "avg_latency_ms",
        "avg_format_compliance",
        "avg_char_f1",
    )
    return {key: value[key] for key in allowed_keys if key in value}


def _iter_summary_files() -> Iterable[Path]:
    if not REPORTS_ROOT.exists():
        return []
    return sorted(REPORTS_ROOT.glob("openclaw-feishu-*/summary.json"))


def _summary_record(path: Path) -> Dict[str, Any]:
    summary = _read_json(path)
    return {
        "scenario": summary.get("scenario", ""),
        "report_dir": str(path.parent.relative_to(REPO_ROOT)),
        "trace_token_round_1": summary.get("trace_token_round_1", ""),
        "trace_token_round_2": summary.get("trace_token_round_2", ""),
        "request_id": summary.get("request_id", ""),
        "status_code": summary.get("status_code"),
        "passed": bool(summary.get("passed", False)),
        "checks": summary.get("checks", {}),
    }


def _feishu_acceptance_payload() -> Dict[str, Any]:
    all_runs = [_summary_record(path) for path in _iter_summary_files()]
    latest_passed_by_scenario: Dict[str, Dict[str, Any]] = {}
    for record in all_runs:
        scenario = str(record.get("scenario") or "")
        if not scenario or not record.get("passed"):
            continue
        latest_passed_by_scenario[scenario] = record
    latest_passed = [
        latest_passed_by_scenario[scenario]
        for scenario in sorted(latest_passed_by_scenario)
    ]
    return {
        "latest_passed": latest_passed,
        "all_run_count": len(all_runs),
        "passed_run_count": sum(1 for record in all_runs if record.get("passed")),
    }


def load_feishu_office_evidence() -> Dict[str, Any]:
    """Load real delivery evidence without returning model answer text."""

    manifest_path = DATA_ROOT / "dataset_manifest.json"
    quality_report_path = DATA_ROOT / "quality_report.json"
    evaluation_path = EVAL_ROOT / "evaluation.json"
    evaluation = _read_json(evaluation_path)
    return {
        "dataset": {
            "manifest": _read_json(manifest_path),
            "quality_report": _read_json(quality_report_path),
            "artifact_paths": {
                "manifest": str(manifest_path.relative_to(REPO_ROOT)),
                "quality_report": str(quality_report_path.relative_to(REPO_ROOT)),
            },
        },
        "evaluation": {
            "baseline": _sanitize_model_evaluation(evaluation.get("baseline", {})),
            "tuned": _sanitize_model_evaluation(evaluation.get("tuned", {})),
            "artifact_paths": {
                "evaluation_json": str(evaluation_path.relative_to(REPO_ROOT)),
                "evaluation_markdown": str(
                    (EVAL_ROOT / "evaluation.md").relative_to(REPO_ROOT)
                ),
            },
        },
        "feishu_acceptance": _feishu_acceptance_payload(),
    }
