#!/usr/bin/env python3
"""Shared runtime config helpers for deployment scripts and unit tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List


REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_FRAGMENTS = ("/path/to/", "placeholder", "change-me", "changeme")


def load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def resolve_model_config_path(repo_root: Path, env_values: Dict[str, str]) -> Path:
    configured = (
        os.getenv("MODELS_CONFIG_FILE")
        or env_values.get("MODELS_CONFIG_FILE")
        or "models.json"
    )
    candidate = Path(configured)
    if candidate.is_absolute():
        return candidate
    return repo_root / candidate


def is_placeholder_cli_path(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    return any(fragment in normalized for fragment in PLACEHOLDER_FRAGMENTS)


def find_model_problems(model_config_path: Path) -> List[str]:
    try:
        payload = json.loads(model_config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"failed to parse {model_config_path}: {exc}"]

    models = payload.get("models")
    if not isinstance(models, list) or not models:
        return [f"{model_config_path} must contain a non-empty 'models' list"]

    problems: List[str] = []
    for index, model in enumerate(models, start=1):
        if not isinstance(model, dict):
            problems.append(f"models[{index - 1}] must be an object")
            continue

        model_id = str(model.get("id") or f"models[{index - 1}]").strip()
        tool = model.get("tool")
        if not isinstance(tool, dict):
            problems.append(f"model '{model_id}' is missing a 'tool' object")
            continue

        cli_path = str(tool.get("cli_path", "")).strip()
        if is_placeholder_cli_path(cli_path):
            problems.append(
                f"model '{model_id}' still uses a placeholder cli_path: {cli_path or '<empty>'}"
            )
            continue

        target = Path(cli_path)
        if not target.exists():
            problems.append(f"model '{model_id}' points to a missing cli_path: {cli_path}")
        elif not os.access(target, os.X_OK):
            problems.append(f"model '{model_id}' points to a non-executable cli_path: {cli_path}")

    return problems
