import json
from pathlib import Path

from competition.feishu_office.dataset_pipeline import (
    DEFAULT_OUTPUT_DIR,
    TASK_ORDER,
    validate_materialized_dataset,
)


def test_materialized_dataset_contract():
    result = validate_materialized_dataset(DEFAULT_OUTPUT_DIR)
    assert result["rows_by_split"]["train"] >= 1000
    assert result["rows_by_split"]["validation"] + result["rows_by_split"]["test"] >= 200
    assert set(result["rows_by_task"]) == set(TASK_ORDER)


def test_quality_report_and_manifest_exist():
    manifest = json.loads((DEFAULT_OUTPUT_DIR / "dataset_manifest.json").read_text(encoding="utf-8"))
    quality = json.loads((DEFAULT_OUTPUT_DIR / "quality_report.json").read_text(encoding="utf-8"))
    assert manifest["row_count"] >= 1200
    assert quality["rows_by_split"]["train"] >= 1000


def test_source_splits_are_disjoint():
    all_rows = [
        json.loads(line)
        for line in (DEFAULT_OUTPUT_DIR / "all.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    source_sets = {"train": set(), "validation": set(), "test": set()}
    for row in all_rows:
        source_sets[row["split"]].add(row["source_url"])
    assert source_sets["train"].isdisjoint(source_sets["validation"])
    assert source_sets["train"].isdisjoint(source_sets["test"])
    assert source_sets["validation"].isdisjoint(source_sets["test"])

