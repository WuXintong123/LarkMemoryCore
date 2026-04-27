#!/usr/bin/env python3
# ===- real_inference_benchmark.py --------------------------------------------
#
# SPDX-License-Identifier: Apache-2.0
#
# ===---------------------------------------------------------------------------
#
# Real benchmark runner using a real dataset against a running API server.
# No mock backend or synthetic responses are used.
#
# Required environment variables:
#   REAL_DATASET_PATH
#   REAL_INTEGRATION_MODEL
#
# Optional:
#   REAL_INTEGRATION_BASE_URL (default: http://127.0.0.1:8000)
#   REAL_INTEGRATION_API_KEY
#   BENCHMARK_MAX_SAMPLES (default: 50)
#   BENCHMARK_TIMEOUT_S (default: 120)
# ===---------------------------------------------------------------------------

import json
import os
import statistics
import time
from typing import Any, Dict, List

import requests


BASE_URL = os.getenv("REAL_INTEGRATION_BASE_URL", "http://127.0.0.1:8000")
MODEL_ID = os.getenv("REAL_INTEGRATION_MODEL", "").strip()
DATASET_PATH = os.getenv("REAL_DATASET_PATH", "").strip()
API_KEY = os.getenv("REAL_INTEGRATION_API_KEY", "").strip()
MAX_SAMPLES = int(os.getenv("BENCHMARK_MAX_SAMPLES", "50"))
TIMEOUT_S = float(os.getenv("BENCHMARK_TIMEOUT_S", "120"))


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    return headers


def _load_dataset(path: str) -> List[Dict[str, Any]]:
    if not path:
        raise RuntimeError("REAL_DATASET_PATH is required")
    if not os.path.exists(path):
        raise RuntimeError(f"REAL_DATASET_PATH does not exist: {path}")

    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if not isinstance(row, dict):
                raise RuntimeError(f"line {line_no}: dataset row must be JSON object")
            prompt = row.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise RuntimeError(
                    f"line {line_no}: dataset row must include non-empty 'prompt'"
                )
            rows.append(row)

    if not rows:
        raise RuntimeError("dataset is empty")
    return rows


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    low = int(k)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    weight = k - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def main() -> None:
    if not MODEL_ID:
        raise RuntimeError("REAL_INTEGRATION_MODEL is required")

    dataset = _load_dataset(DATASET_PATH)[: max(1, MAX_SAMPLES)]

    latencies_ms: List[float] = []
    prompt_tokens = 0
    completion_tokens = 0
    failures = 0

    run_start = time.perf_counter()
    for idx, row in enumerate(dataset):
        payload = {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": row["prompt"]}],
            "max_tokens": int(row.get("max_tokens", 128)),
            "temperature": float(row.get("temperature", 0.0)),
        }

        req_start = time.perf_counter()
        response = requests.post(
            f"{BASE_URL}/v1/chat/completions",
            headers=_headers(),
            json=payload,
            timeout=TIMEOUT_S,
        )
        elapsed_ms = (time.perf_counter() - req_start) * 1000.0
        latencies_ms.append(elapsed_ms)

        if response.status_code != 200:
            failures += 1
            print(f"[FAIL] sample={idx} status={response.status_code} body={response.text}")
            continue

        body = response.json()
        usage = body.get("usage", {})
        prompt_tokens += int(usage.get("prompt_tokens", 0))
        completion_tokens += int(usage.get("completion_tokens", 0))

    total_time_s = max(1e-6, time.perf_counter() - run_start)
    success = len(dataset) - failures

    print("=== Real Inference Benchmark ===")
    print(f"base_url: {BASE_URL}")
    print(f"model: {MODEL_ID}")
    print(f"samples_total: {len(dataset)}")
    print(f"samples_success: {success}")
    print(f"samples_failed: {failures}")
    print(f"total_time_s: {total_time_s:.3f}")
    print(f"qps: {success / total_time_s:.3f}")
    print(f"latency_avg_ms: {statistics.mean(latencies_ms):.2f}")
    print(f"latency_p50_ms: {_percentile(latencies_ms, 0.50):.2f}")
    print(f"latency_p90_ms: {_percentile(latencies_ms, 0.90):.2f}")
    print(f"latency_p99_ms: {_percentile(latencies_ms, 0.99):.2f}")
    print(f"prompt_tokens_total: {prompt_tokens}")
    print(f"completion_tokens_total: {completion_tokens}")
    if total_time_s > 0:
        print(
            "generated_tokens_per_second: "
            f"{completion_tokens / total_time_s:.3f}"
        )


if __name__ == "__main__":
    main()
