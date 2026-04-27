"""Evaluate baseline and tuned models on held-out Feishu Office samples."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import requests


SECTION_RULES = {
    "knowledge_qa": ("结论：", "依据："),
    "information_summary": ("摘要：",),
    "meeting_minutes": ("会议主题：", "背景：", "讨论要点：", "待办事项："),
    "weekly_report": ("本周进展：", "风险与关注：", "下周计划："),
    "standardized_response": ("标准回复：",),
}


def _load_rows(path: Path, limit: int) -> List[Dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    rows.sort(key=lambda row: len(row["input"]))
    return rows[:limit]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _char_f1(prediction: str, target: str) -> float:
    pred_counter: Dict[str, int] = {}
    target_counter: Dict[str, int] = {}
    for char in _normalize(prediction):
        pred_counter[char] = pred_counter.get(char, 0) + 1
    for char in _normalize(target):
        target_counter[char] = target_counter.get(char, 0) + 1
    overlap = 0
    for char, count in pred_counter.items():
        overlap += min(count, target_counter.get(char, 0))
    precision = overlap / max(1, sum(pred_counter.values()))
    recall = overlap / max(1, sum(target_counter.values()))
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _format_compliance(task: str, text: str) -> float:
    sections = SECTION_RULES[task]
    present = sum(1 for section in sections if section in text)
    return present / len(sections)


def _evaluate_model(
    *,
    base_url: str,
    api_key: str,
    model_id: str,
    rows: List[Dict[str, Any]],
    request_timeout_s: float,
    max_tokens: int,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    results = []
    failures = []
    for row in rows:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": row["input"]}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        started_at = time.perf_counter()
        try:
            response = requests.post(
                f"{base_url}/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=request_timeout_s,
            )
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            response.raise_for_status()
            body = response.json()
            output = body["choices"][0]["message"]["content"]
        except Exception as exc:
            failures.append({"id": row["id"], "task": row["task"], "error": str(exc)})
            continue
        results.append(
            {
                "id": row["id"],
                "task": row["task"],
                "latency_ms": latency_ms,
                "format_compliance": _format_compliance(row["task"], output),
                "char_f1": _char_f1(output, row["output"]),
                "output": output,
            }
        )

    avg_latency = sum(item["latency_ms"] for item in results) / max(1, len(results))
    avg_format = sum(item["format_compliance"] for item in results) / max(1, len(results))
    avg_f1 = sum(item["char_f1"] for item in results) / max(1, len(results))
    return {
        "model_id": model_id,
        "sample_count": len(rows),
        "success_count": len(results),
        "failure_count": len(failures),
        "avg_latency_ms": round(avg_latency, 2),
        "avg_format_compliance": round(avg_format, 4),
        "avg_char_f1": round(avg_f1, 4),
        "samples": results,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate baseline and tuned models.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--baseline-model", required=True)
    parser.add_argument("--tuned-model", required=True)
    parser.add_argument("--test-file", type=Path, default=Path("competition/feishu_office/data/test.jsonl"))
    parser.add_argument("--sample-count", type=int, default=20)
    parser.add_argument("--request-timeout-s", type=float, default=180.0)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    rows = _load_rows(args.test_file, args.sample_count)
    baseline = _evaluate_model(
        base_url=args.base_url,
        api_key=args.api_key,
        model_id=args.baseline_model,
        rows=rows,
        request_timeout_s=args.request_timeout_s,
        max_tokens=args.max_tokens,
    )
    tuned = _evaluate_model(
        base_url=args.base_url,
        api_key=args.api_key,
        model_id=args.tuned_model,
        rows=rows,
        request_timeout_s=args.request_timeout_s,
        max_tokens=args.max_tokens,
    )
    report = {"baseline": baseline, "tuned": tuned}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_md.write_text(
        "\n".join(
            [
                "# Feishu Office Evaluation",
                "",
                f"- Baseline model: `{baseline['model_id']}`",
                f"- Tuned model: `{tuned['model_id']}`",
                f"- Sample count: {baseline['sample_count']}",
                f"- Baseline success/failure: {baseline['success_count']}/{baseline['failure_count']}",
                f"- Tuned success/failure: {tuned['success_count']}/{tuned['failure_count']}",
                "",
                "| Model | Avg latency (ms) | Avg format compliance | Avg char F1 |",
                "| --- | ---: | ---: | ---: |",
                f"| {baseline['model_id']} | {baseline['avg_latency_ms']} | {baseline['avg_format_compliance']} | {baseline['avg_char_f1']} |",
                f"| {tuned['model_id']} | {tuned['avg_latency_ms']} | {tuned['avg_format_compliance']} | {tuned['avg_char_f1']} |",
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
