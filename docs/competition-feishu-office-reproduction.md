# Feishu Office Assistant Full Reproduction Guide

## Goal

This document is the single source of truth for reproducing the exact
competition workflow that was executed on `buddy-ascend`.

It covers:

1. code build
2. real model CLI validation
3. real dataset construction
4. GPU training environment setup
5. QLoRA adapter training
6. competition runtime startup
7. baseline / tuned real integration
8. baseline / tuned real benchmark
9. OpenClaw + Feishu four-scenario acceptance

This guide uses exact paths and exact commands that were verified on the
server. Do not replace them with angle-bracket placeholders.

## Exact Environment

Run everything on `buddy-ascend`.

```bash
ssh buddy-ascend
```

Define the exact paths once:

```bash
export REPO_ROOT=/home/huangyiheng/src/lark-memory-core-feishu-live-20260416
export BUDDY_MLIR_ROOT=/home/huangyiheng/buddy-mlir
export TRAIN_VENV=$HOME/.venvs/lark-memory-feishu-office
export OPENCLAW_ENV=$HOME/.openclaw/.env
export OPENCLAW_BIN=$HOME/.nvm/versions/node/v22.22.2/bin/openclaw
```

Enter the repository and load Node for OpenClaw:

```bash
cd "$REPO_ROOT"
source ~/.nvm/nvm.sh
```

## 1. Build And Unit-Test Gate

Run these commands exactly:

```bash
cd "$REPO_ROOT"
cmake --preset linux-debug
cmake --build --preset linux-debug-build --target generate_python_proto compute_server compute_server_tests
ctest --preset linux-debug-test --output-on-failure --label-regex "cpp|python-unit"
python3 -m pytest -q tests/python
python3 -m pytest -q tests/python/test_production_behaviors.py -k "openclaw or feishu or latest_user or debug_trace"
python3 -m pytest -q tests/python/test_grpc_contracts.py
python3 -m pytest -q tests/python/test_feishu_office_dataset_contracts.py
FEISHU_OFFICE_DAEMON_PORT=19600 FEISHU_OFFICE_TRAIN_PYTHON=$TRAIN_VENV/bin/python \
  python3 -m pytest -q tests/python/test_feishu_office_runtime_contracts.py
```

Expected result:

- all listed commands pass
- `ctest` reports `100% tests passed`

## 2. Real Model CLI Validation

Do not use any placeholder path here. Use the exact verified path below:

```bash
cd "$BUDDY_MLIR_ROOT"
ninja -C build buddy-deepseek-r1-cli
printf "Say READY only.\n" | ./build/bin/buddy-deepseek-r1-cli --max-tokens=4 --no-stats
```

Expected result:

- the binary exists at `/home/huangyiheng/buddy-mlir/build/bin/buddy-deepseek-r1-cli`
- the command returns a short READY-style answer instead of a shell or file error

## 3. Build The Competition Dataset

Build and validate the exact real dataset used in the final run:

```bash
cd "$REPO_ROOT"
python3 -m competition.feishu_office.build_dataset
python3 -m competition.feishu_office.validate_dataset
```

Expected result:

- `competition/feishu_office/data/all.jsonl` exists
- validation prints a JSON summary
- the summary contains:
  - `row_count: 1595`
  - `train: 1070`
  - `validation: 180`
  - `test: 345`

## 4. Prepare The GPU Training Environment

Create the exact venv and install the exact runtime stack:

```bash
cd "$REPO_ROOT"
./ops/feishu_office_train_env.sh
```

Expected result:

- the venv exists at `$HOME/.venvs/lark-memory-feishu-office`
- the script prints:
  - `torch 2.5.1+cu121`
  - `cuda_available True`
  - `device NVIDIA GeForce RTX 3090`

## 5. Train The Adapter

Run the exact successful training command:

```bash
cd "$REPO_ROOT"
rm -rf competition/feishu_office/artifacts/adapter
$TRAIN_VENV/bin/python competition/feishu_office/train_qlora.py \
  --output-dir competition/feishu_office/artifacts/adapter \
  --max-steps 60 \
  --gradient-accumulation-steps 8 \
  --train-batch-size 1
```

Expected result:

- training completes successfully
- `competition/feishu_office/artifacts/adapter/run_summary.json` exists
- the summary contains:
  - `final_train_loss: 0.02729497868567705`
  - `cuda_available: true`
  - `cuda_device: NVIDIA GeForce RTX 3090`

## 6. Start The Competition Runtime

Run the exact preflight and startup sequence:

```bash
cd "$REPO_ROOT"
./ops/feishu_office_competition_preflight.sh
./ops/feishu_office_competition_start.sh
```

Export the runtime values used by all later commands:

```bash
export LARK_MEMORY_CORE_API_KEY=$(cat "$REPO_ROOT/.run/feishu-office-competition/runtime/api_key.txt")
export LARK_MEMORY_CORE_FEISHU_API_LOG_PATH=$REPO_ROOT/.run/feishu-office-competition/logs/api.log
export LARK_MEMORY_CORE_FEISHU_COMPUTE_LOG_PATH=$REPO_ROOT/.run/feishu-office-competition/logs/compute.log
```

Verify health:

```bash
curl -fsS http://127.0.0.1:18100/health
curl -fsS http://127.0.0.1:18100/ready
curl -fsS -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" http://127.0.0.1:18100/v1/models
```

Expected result:

- `health` returns `{"status":"healthy"}`
- `v1/models` lists both:
  - `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B`
  - `lark-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice`

## 7. Real Integration

Important:

- run these commands sequentially
- do not run the baseline and tuned checks at the same time
- the competition runtime is intentionally configured with `MAX_COMPUTE_CONCURRENCY=1`

Baseline:

```bash
cd "$REPO_ROOT"
export REAL_DATASET_PATH=$REPO_ROOT/tests/real_data/huangyiheng_2026_02_real.jsonl
export REAL_INTEGRATION_BASE_URL=http://127.0.0.1:18100
export REAL_INTEGRATION_API_KEY=$LARK_MEMORY_CORE_API_KEY
export REAL_INTEGRATION_MAX_SAMPLES=1
export REAL_INTEGRATION_TIMEOUT_S=180
export REAL_INTEGRATION_MODEL=deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
python3 -m pytest -q -m real_integration tests/integration_real/test_real_dataset_inference.py --maxfail=1
```

Tuned:

```bash
cd "$REPO_ROOT"
export REAL_DATASET_PATH=$REPO_ROOT/tests/real_data/huangyiheng_2026_02_real.jsonl
export REAL_INTEGRATION_BASE_URL=http://127.0.0.1:18100
export REAL_INTEGRATION_API_KEY=$LARK_MEMORY_CORE_API_KEY
export REAL_INTEGRATION_MAX_SAMPLES=1
export REAL_INTEGRATION_TIMEOUT_S=180
export REAL_INTEGRATION_MODEL=lark-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice
python3 -m pytest -q -m real_integration tests/integration_real/test_real_dataset_inference.py --maxfail=1
```

Expected result:

- both runs end with `2 passed`

## 8. Real Benchmark

Run benchmarks sequentially, never in parallel.

Baseline:

```bash
cd "$REPO_ROOT"
export REAL_DATASET_PATH=$REPO_ROOT/tests/real_data/huangyiheng_2026_02_real.jsonl
export REAL_INTEGRATION_BASE_URL=http://127.0.0.1:18100
export REAL_INTEGRATION_API_KEY=$LARK_MEMORY_CORE_API_KEY
export BENCHMARK_MAX_SAMPLES=1
export REAL_INTEGRATION_MODEL=deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
python3 benchmarks/real_inference_benchmark.py
```

Tuned:

```bash
cd "$REPO_ROOT"
export REAL_DATASET_PATH=$REPO_ROOT/tests/real_data/huangyiheng_2026_02_real.jsonl
export REAL_INTEGRATION_BASE_URL=http://127.0.0.1:18100
export REAL_INTEGRATION_API_KEY=$LARK_MEMORY_CORE_API_KEY
export BENCHMARK_MAX_SAMPLES=1
export REAL_INTEGRATION_MODEL=lark-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice
python3 benchmarks/real_inference_benchmark.py
```

Observed reference numbers from the verified run:

- baseline latency: `75559.45 ms`
- tuned latency: `4114.18 ms`

## 9. OpenClaw Model Switching

Before each Feishu scenario, set the exact model:

Non-stream:

```bash
source ~/.nvm/nvm.sh
openclaw models set lark_memory_core/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
openclaw gateway restart
openclaw models list
```

Stream:

```bash
source ~/.nvm/nvm.sh
openclaw models set lark_memory_stream/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
openclaw gateway restart
openclaw models list
```

Expected result:

- non-stream scenarios show `lark_memory_core/... default`
- stream scenarios show `lark_memory_stream/... default`

## 10. Feishu Acceptance

Run the scenarios in this exact order:

1. DM + non-stream
2. DM + stream
3. Group `@bot` + non-stream
4. Group `@bot` + stream

### Critical Timing Rule

For both streaming scenarios:

- do not press Enter in the check script immediately after sending the second message
- first wait until the second-round bot reply has fully finished in Feishu
- then confirm the compute slot is idle:

```bash
curl -fsS -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" http://127.0.0.1:18100/v1/admin/metrics
```

Only continue when:

- `active_compute_slots` is `0`
- `queued_requests` is `0`

If you press Enter too early, the first check may show `FAIL` even though the
service is correct. In that case, rerun the same check command with the same
trace tokens after the slot returns to idle.

### 10.1 DM + Non-Stream

Start the check:

```bash
cd "$REPO_ROOT"
./ops/openclaw_feishu_buddy_ascend_check.sh \
  --scenario dm-nonstream \
  --trace-token DM-NS-1-20260418-A \
  --trace-token DM-NS-2-20260418-B
```

Send these exact two DM messages to `LarkMemoryCore Test Bot`:

```text
DM-NS-1-20260418-A 请只回复“收到”。
DM-NS-2-20260418-B 请用一句话总结本轮需求。
```

Expected summary path:

```text
reports/openclaw-feishu-20260418-045903-dm-nonstream/summary.md
```

Expected result: `Overall: PASS`

### 10.2 DM + Stream

Start the check:

```bash
cd "$REPO_ROOT"
./ops/openclaw_feishu_buddy_ascend_check.sh \
  --scenario dm-stream \
  --trace-token DM-S-1-20260418-A \
  --trace-token DM-S-2-20260418-B
```

Send these exact two DM messages:

```text
DM-S-1-20260418-A 请只回复“收到”。
DM-S-2-20260418-B 请用一句话总结本轮需求。
```

Verified summary path:

```text
reports/openclaw-feishu-20260418-052933-dm-stream/summary.md
```

Expected result: `Overall: PASS`

### 10.3 Group @bot + Non-Stream

Start the check:

```bash
cd "$REPO_ROOT"
./ops/openclaw_feishu_buddy_ascend_check.sh \
  --scenario group-at-nonstream \
  --trace-token GROUP-NS-1-20260418-A \
  --trace-token GROUP-NS-2-20260418-B
```

Send these exact two group messages:

```text
@LarkMemoryCore Test Bot GROUP-NS-1-20260418-A 请只回复“收到”。
@LarkMemoryCore Test Bot GROUP-NS-2-20260418-B 请用一句话总结本轮需求。
```

Verified summary path:

```text
reports/openclaw-feishu-20260418-085833-group-at-nonstream/summary.md
```

Expected result: `Overall: PASS`

### 10.4 Group @bot + Stream

Start the check:

```bash
cd "$REPO_ROOT"
./ops/openclaw_feishu_buddy_ascend_check.sh \
  --scenario group-at-stream \
  --trace-token GROUP-S-1-20260418-A \
  --trace-token GROUP-S-2-20260418-B
```

Send these exact two group messages:

```text
@LarkMemoryCore Test Bot GROUP-S-1-20260418-A 请只回复“收到”。
@LarkMemoryCore Test Bot GROUP-S-2-20260418-B 请用一句话总结本轮需求。
```

Verified summary path:

```text
reports/openclaw-feishu-20260418-091110-group-at-stream/summary.md
```

Expected result: `Overall: PASS`

## 11. Final Files That Must Exist

These are the minimum final artifacts:

- `competition/feishu_office/data/`
- `competition/feishu_office/artifacts/adapter/`
- `competition/feishu_office/artifacts/eval/`
- `docs/competition-feishu-office-dataset.md`
- `docs/competition-feishu-office-training-report.md`
- `docs/competition-feishu-office-effect-report.md`
- `docs/competition-feishu-office-demo.md`
- `docs/openclaw-feishu-verification-note-2026-04-18.md`
- the four `reports/openclaw-feishu-20260418-*` directories

## 12. Final Stop Command

After all reproduction steps are complete:

```bash
cd "$REPO_ROOT"
./ops/feishu_office_competition_stop.sh
```
