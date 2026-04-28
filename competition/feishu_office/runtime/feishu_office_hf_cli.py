"""CLI shim for the persistent Feishu Office HF daemon."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any, Dict


def _recv_line(stream: Any) -> Dict[str, Any]:
    line = stream.readline()
    if not line:
        raise RuntimeError("Daemon closed the connection unexpectedly")
    return json.loads(line.decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Client shim for the Feishu Office daemon.")
    parser.add_argument("--daemon-host", default="127.0.0.1")
    parser.add_argument("--daemon-port", type=int, default=19600)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--ping", action="store_true")
    args = parser.parse_args()

    prompt = "" if args.ping else sys.stdin.read()
    request = {
        "type": "ping" if args.ping else "generate",
        "prompt": prompt,
        "max_tokens": args.max_tokens,
    }

    with socket.create_connection(
        (args.daemon_host, args.daemon_port),
        timeout=args.timeout_s,
    ) as sock:
        sock.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        stream = sock.makefile("rb")
        while True:
            message = _recv_line(stream)
            message_type = message.get("type")
            if message_type == "pong":
                return 0
            if message_type == "chunk":
                sys.stdout.write(message.get("text", ""))
                sys.stdout.flush()
                continue
            if message_type == "done":
                return 0
            if message_type == "error":
                sys.stderr.write(message.get("message", "unknown daemon error") + "\n")
                sys.stderr.flush()
                return 1
            raise RuntimeError(f"Unsupported daemon message type: {message_type}")


if __name__ == "__main__":
    raise SystemExit(main())
