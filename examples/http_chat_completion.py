#!/usr/bin/env python3
"""Low-level HTTP example for smoke tests and transport debugging."""

import argparse
import json
import os
import sys

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Introduce RISC-V architecture.",
        help="Prompt text to send to the local API.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("API_BASE_URL", "http://localhost:8000"),
        help="Base URL for the API server.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL_ID", "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"),
        help="Model ID exposed by /v1/models.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("API_KEY", ""),
        help="Optional bearer token.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("MAX_TOKENS", "16")),
        help="Upper bound for generated tokens.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Use the streaming SSE path instead of a JSON response.",
    )
    return parser.parse_args()


def build_headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def run_stream(base_url: str, headers: dict, payload: dict) -> int:
    with requests.post(
        f"{base_url}/v1/chat/completions",
        headers=headers,
        json=payload,
        stream=True,
        timeout=600,
    ) as response:
        if response.status_code != 200:
            print(response.text, file=sys.stderr)
            return 1

        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue
            data = decoded[6:]
            if data == "[DONE]":
                print("\n[DONE]")
                return 0
            chunk = json.loads(data)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                print(content, end="", flush=True)
    return 0


def run_json(base_url: str, headers: dict, payload: dict) -> int:
    response = requests.post(
        f"{base_url}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=600,
    )
    if response.status_code != 200:
        print(response.text, file=sys.stderr)
        return 1

    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    args = parse_args()
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "max_tokens": args.max_tokens,
        "stream": args.stream,
    }
    headers = build_headers(args.api_key)
    if args.stream:
        return run_stream(args.base_url, headers, payload)
    return run_json(args.base_url, headers, payload)


if __name__ == "__main__":
    raise SystemExit(main())
