# Feishu Office Assistant Training Report

## Goal

Tune `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B` for Feishu office tasks while
keeping the existing LarkMemoryCore HTTP and gRPC contract unchanged.

## Method

- training method: QLoRA
- hardware target: single RTX 3090 on `buddy-ascend`
- serving integration: persistent local daemon + compute-compatible CLI shim
- runtime contract: unchanged stdin/stdout CLI contract for `compute_server`

## Environment

- environment bootstrap script: `ops/feishu_office_train_env.sh`
- training requirements: `competition/feishu_office/requirements-train.txt`
- training entrypoint: `competition/feishu_office/train_qlora.py`

The training venv is intentionally isolated from the system `python3` used by
the existing API runtime.

## Hyperparameters

The current default training configuration is:

- max sequence length: 1536
- max steps: 120
- learning rate: `2e-4`
- per-device batch size: `1`
- gradient accumulation steps: `8`
- LoRA rank: `64`
- LoRA alpha: `128`
- LoRA dropout: `0.05`

## Outputs

Training outputs are written to `competition/feishu_office/artifacts/adapter/`
and include:

- adapter weights
- tokenizer snapshot
- `run_summary.json`
- training arguments snapshot

## Executed Run

- Date: 2026-04-18
- Host: `buddy-ascend`
- GPU: `NVIDIA GeForce RTX 3090`
- Base model: `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B`
- Train rows: `1070`
- Validation rows: `180`
- Command:

```bash
cd /home/huangyiheng/src/lark-memory-core-feishu-live-20260416
$HOME/.venvs/lark-memory-feishu-office/bin/python \
  competition/feishu_office/train_qlora.py \
  --output-dir competition/feishu_office/artifacts/adapter \
  --max-steps 60 \
  --gradient-accumulation-steps 8 \
  --train-batch-size 1
```

- Final train loss: `0.02729497868567705`
- Train runtime: `234.867 s`
- Train steps per second: `0.255`
- Train samples per second: `2.044`
- Output adapter path: `competition/feishu_office/artifacts/adapter`

## Run Notes

- The first training attempt failed because `TrainingArguments` in the installed
  `transformers 4.56.2` build uses `eval_strategy` instead of
  `evaluation_strategy`.
- The second training attempt failed during online evaluation because the
  default collator did not pad variable-length validation batches.
- The final successful run disabled online evaluation and kept checkpoint saves,
  then delegated held-out comparison to the separate evaluation script.

## Report Update Rule

After each real training run, update this file with:

- exact command line
- final train loss
- wall-clock duration
- GPU details
- output adapter path
- failure / retry notes if any
