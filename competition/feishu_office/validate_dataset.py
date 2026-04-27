"""Entry point for validating the Feishu Office Assistant dataset."""

from __future__ import annotations

import json

from .dataset_pipeline import DEFAULT_OUTPUT_DIR, validate_materialized_dataset


if __name__ == "__main__":
    print(json.dumps(validate_materialized_dataset(DEFAULT_OUTPUT_DIR), ensure_ascii=False, indent=2))
