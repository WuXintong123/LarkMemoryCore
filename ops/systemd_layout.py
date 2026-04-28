#!/usr/bin/env python3
"""Resolve local systemd unit layout for single-node deployments."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Dict, List

if __package__:
    from .runtime_paths import REPO_ROOT, load_env_file
else:
    from runtime_paths import REPO_ROOT, load_env_file


LOCAL_HOSTS = {"127.0.0.1", "0.0.0.0", "localhost", "::1", "[::1]"}


def _split_host_port(address: str) -> tuple[str, int]:
    value = address.strip()
    if not value:
        raise ValueError("empty address")
    if value.startswith("[") and "]:" in value:
        host, _, port = value.rpartition("]:")
        return host + "]", int(port)
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port:
        raise ValueError(f"invalid address: {address}")
    return host, int(port)


def _is_local_host(host: str) -> bool:
    return host.strip().lower() in LOCAL_HOSTS

def build_layout(repo_root: Path, env_values: Dict[str, str]) -> dict:
    compute_address = env_values.get("COMPUTE_SERVER_ADDRESS", "0.0.0.0:9000").strip() or "0.0.0.0:9000"
    primary_host, primary_port = _split_host_port(compute_address)

    compute_units = [
        {
            "unit": "lark-memory-core-compute.service",
            "service_name": "lark-memory-core-compute.service",
            "kind": "primary",
            "node_id": "default",
            "instance_name": "",
            "grpc_address": compute_address,
            "host": primary_host,
            "port": primary_port,
            "local": _is_local_host(primary_host),
            "env_file": "",
            "log_path": "%h/lark-memory-core/.run/systemd-compute.log",
        }
    ]

    managed_units = [item["unit"] for item in compute_units]
    managed_units.extend(["lark-memory-core-api.service", "lark-memory-core-proxy.service"])

    managed_log_paths = [item["log_path"] for item in compute_units]
    managed_log_paths.extend(
        [
            "%h/lark-memory-core/.run/systemd-api.log",
            "%h/lark-memory-core/.run/systemd-proxy.log",
        ]
    )

    return {
        "compute_units": compute_units,
        "managed_units": managed_units,
        "managed_log_paths": managed_log_paths,
        "local_compute_ports": [item["port"] for item in compute_units if item["local"]],
    }


def command_json(layout: dict) -> int:
    print(json.dumps(layout, indent=2, ensure_ascii=False))
    return 0


def command_units(layout: dict) -> int:
    for unit in layout["managed_units"]:
        print(unit)
    return 0


def command_compute_units(layout: dict) -> int:
    for item in layout["compute_units"]:
        print(item["unit"])
    return 0


def command_ports(layout: dict) -> int:
    for port in layout["local_compute_ports"]:
        print(port)
    return 0


def command_log_paths(layout: dict) -> int:
    for path in layout["managed_log_paths"]:
        print(path)
    return 0


def command_write_target(layout: dict, target_path: Path) -> int:
    units = layout["managed_units"]
    wants = " ".join(units)
    target_path.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=LarkMemoryCore Stack",
                f"Wants={wants}",
                f"After={wants}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "json",
            "units",
            "compute-units",
            "ports",
            "log-paths",
            "write-target",
        ],
    )
    parser.add_argument("path", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    env_values = load_env_file(REPO_ROOT / ".env")
    layout = build_layout(REPO_ROOT, env_values)

    if args.command == "json":
        return command_json(layout)
    if args.command == "units":
        return command_units(layout)
    if args.command == "compute-units":
        return command_compute_units(layout)
    if args.command == "ports":
        return command_ports(layout)
    if args.command == "log-paths":
        return command_log_paths(layout)
    if args.command == "write-target":
        if not args.path:
            raise SystemExit("write-target requires a target file path")
        return command_write_target(layout, Path(args.path))
    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
