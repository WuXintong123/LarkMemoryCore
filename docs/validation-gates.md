# Validation Gates

## Claim Rules

Do not claim a change is compile-tested, smoke-tested, or deployment-verified unless the corresponding commands were actually run and passed.

## Baseline Build and Unit-Test Gate

Run from the repository root:

```bash
cmake --preset linux-debug
cmake --build --preset linux-debug-build --target generate_python_proto compute_server compute_server_tests
ctest --preset linux-debug-test --output-on-failure --label-regex "cpp|python-unit"
```

This gate covers:

- root configure and dependency discovery
- Python protobuf generation
- C++ unit tests
- Python API unit tests

## Python API Unit Gate

`tests/python` focuses on API behavior and contracts. It does **not** require a real model backend.

Direct path:

```bash
python3 -m pytest -q tests/python
```

Focused decision-memory gate:

```bash
python3 -m pytest -q tests/python/test_memory_engine.py
```

This gate uses existing repository and Feishu Office real-data artifacts to verify
decision extraction, anti-interference retrieval, contradiction updates, and
prompt-context efficiency metrics.

## Automated gRPC Contract Gate

When a change touches request formatting, the Python API layer, or gRPC request/response mapping, run the focused gRPC contract gate explicitly:

```bash
python3 -m pytest -q tests/python/test_grpc_contracts.py
```

This gate verifies:

- `buddy_deepseek_r1` chat messages are rendered into the final backend prompt before the gRPC call
- gRPC `ProcessRequest` fields are populated correctly for non-streaming inference
- gRPC `ProcessResponse` output, request id, and usage stats are mapped back into the OpenAI-style HTTP response

For prompt-consistency comparisons, compare direct CLI output with the same
final prompt string that the API sends over gRPC. For `buddy_deepseek_r1`,
chat input `hello!` becomes backend prompt `User: hello!`.

## Real Model Gate

If the change touches the real serving path or real model integration, run the real-model chain explicitly.

Compiler-side direct validation:

```bash
cd /home/huangyiheng/buddy-mlir
ninja -C build buddy-deepseek-r1-cli
printf "Say READY only.\n" | ./build/bin/buddy-deepseek-r1-cli --max-tokens=4 --no-stats
```

Service-side real integration and benchmark:

```bash
cd /home/huangyiheng/src/lark-memory-core-feishu-live-20260416
export REAL_INTEGRATION_MODEL="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
export REAL_DATASET_PATH="/home/huangyiheng/src/lark-memory-core-feishu-live-20260416/tests/real_data/huangyiheng_2026_02_real.jsonl"
export REAL_INTEGRATION_BASE_URL="http://127.0.0.1:18100"
export REAL_INTEGRATION_API_KEY="$(cat /home/huangyiheng/src/lark-memory-core-feishu-live-20260416/.run/feishu-office-competition/runtime/api_key.txt)"
export REAL_INTEGRATION_MAX_SAMPLES=1
export REAL_INTEGRATION_TIMEOUT_S=180

pytest -q -m real_integration tests/integration_real
python3 benchmarks/real_inference_benchmark.py
```

These steps are intentionally outside the default CTest graph because they require:

- a running service
- a real model backend
- a real dataset

## Persistent Deployment Gate

Run on the target Linux host from the deployment copy:

```bash
cd ~/lark-memory-core
./ops/install_user_services.sh
./ops/preflight.sh
./ops/smoke_prod.sh
./ops/status.sh
```

This gate is stricter than the local smoke examples.

Deployment is only verified when:

- `loginctl show-user "$USER" -p Linger` reports `Linger=yes`
- `./ops/smoke_prod.sh` passes
- the configured backends are healthy
- HTTPS health succeeds through Caddy
- a real authenticated completion request succeeds
- the real model integration gate passes
- the real benchmark gate completes
- after disconnecting SSH and reconnecting, the same health and model checks still succeed without restarting services

If `Linger=no`, `./ops/smoke_prod.sh` must fail and the deployment must remain unverified.

## PR and Commit Notes

If any gate was intentionally not run, say so explicitly instead of implying it passed.
