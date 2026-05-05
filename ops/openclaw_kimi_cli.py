#!/usr/bin/env python3
"""CLI bridge: stdin prompt -> OpenClaw local agent (Kimi) -> stdout text.

Designed for LarkMemoryCore compute_server. Logs full stderr/stdout from
the openclaw call to a side log so non-zero exit codes can be diagnosed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LOG_DIR = Path("/home/wuxintong/LarkMemoryCore/.run/feishu-office-competition/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
BRIDGE_LOG = LOG_DIR / "openclaw_kimi_bridge.log"


def log(msg: str) -> None:
    try:
        with BRIDGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw Kimi CLI bridge")
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()

    prompt = sys.stdin.read().strip()
    if not prompt:
        sys.stderr.write("Empty prompt\n")
        log("empty prompt")
        return 1

    openclaw_bin = os.environ.get(
        "OPENCLAW_BIN", "/home/wuxintong/miniconda3/envs/torch2.8/bin/openclaw"
    )

    env = os.environ.copy()
    env.setdefault("HOME", "/home/wuxintong")
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    # Force the openclaw shebang (#!/usr/bin/env node) to resolve to the
    # node that ships with the conda env (v25), not the system /usr/bin/node
    # which is v18 and lacks --disable-warning support.
    node_dir = "/home/wuxintong/miniconda3/envs/torch2.8/bin"
    existing_path = env.get(
        "PATH",
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    )
    if node_dir not in existing_path.split(":"):
        env["PATH"] = f"{node_dir}:{existing_path}"
    else:
        env["PATH"] = existing_path

    cmd = [
        openclaw_bin,
        "agent",
        "--local",
        "--agent",
        "main",
        "--message",
        prompt,
        "--json",
    ]
    log(f"invoke openclaw home={env.get('HOME')} prompt_chars={len(prompt)} cmd={cmd}")
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input="",
            capture_output=True,
            text=True,
            env=env,
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        log("openclaw timeout after 240s")
        sys.stdout.write("openclaw timeout\n")
        return 124
    elapsed = time.time() - started
    log(
        f"openclaw exit={proc.returncode} elapsed={elapsed:.1f}s "
        f"stdout_len={len(proc.stdout or '')} stderr_len={len(proc.stderr or '')}"
    )
    log(f"openclaw stderr: {(proc.stderr or '')[:2000]}")
    log(f"openclaw stdout-head: {(proc.stdout or '')[:400]}")

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        out = (proc.stdout or "").strip()
        sys.stdout.write(
            f"openclaw_bridge_error exit={proc.returncode}\nstderr={err}\nstdout={out}\n"
        )
        return proc.returncode

    try:
        payload = json.loads(proc.stdout)
        text = payload["payloads"][0]["text"]
    except Exception as exc:
        log(f"parse failed: {exc}")
        sys.stdout.write(
            f"openclaw_bridge_parse_error={exc}\nraw={proc.stdout[:1000]}\n"
        )
        return 1

    words = text.split()
    if args.max_tokens > 0 and len(words) > args.max_tokens:
        text = " ".join(words[: args.max_tokens])
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
