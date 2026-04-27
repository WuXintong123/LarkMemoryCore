#!/usr/bin/env python3
"""OpenAI SDK compatibility example.

Install the extra dependency first:
    pip install -r requirements-examples.txt
"""

import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Introduce RISC-V architecture.",
        help="Prompt text to send through the OpenAI SDK.",
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
        default=os.getenv("API_KEY", "sk-local-placeholder"),
        help="Bearer token for the OpenAI SDK client.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("MAX_TOKENS", "16")),
        help="Upper bound for generated tokens.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from openai import OpenAI

    client = OpenAI(api_key=args.api_key, base_url=f"{args.base_url}/v1")

    models = client.models.list()
    print("Available models:")
    for model in models.data:
        print(f"  - {model.id}")

    response = client.chat.completions.create(
        model=args.model,
        messages=[{"role": "user", "content": args.prompt}],
        max_tokens=args.max_tokens,
    )
    print("\nAssistant response:")
    print(response.choices[0].message.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
