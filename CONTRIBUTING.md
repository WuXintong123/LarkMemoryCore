# Contributing / 参与贡献

Thanks for contributing to `lark-memory-core`.

感谢你为 `lark-memory-core` 做贡献。

## Current Supported Scope / 当前支持范围

At the moment, contribution and validation are scoped to the Linux single-node path.

目前贡献与验证范围以 Linux 单机路径为准。

The repository does **not** currently treat Docker, cluster deployment/routing, or macOS deployment/build support as maintained contribution targets.

当前仓库**不**将 Docker、cluster 部署/路由、或 macOS 部署/构建支持视为持续维护的贡献目标。

## Development Setup

Install the Python dependencies that match your active interpreter:

```bash
python3 -m pip install -r requirements-dev.txt
```

Install the host build dependencies before configuring the root CMake graph.

Ubuntu / Debian:

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  ninja-build \
  pkg-config \
  libgrpc++-dev \
  libgrpc-dev \
  libprotobuf-dev \
  protobuf-compiler \
  protobuf-compiler-grpc \
  libssl-dev \
  libgtest-dev \
  nlohmann-json3-dev
```

Configure the root build graph:

```bash
cmake --preset linux-debug
```

## Build and Unit Tests

Generate Python protobuf stubs, build the compute server, and build the C++ unit-test targets:

```bash
cmake --build --preset linux-debug-build --target generate_python_proto compute_server compute_server_tests
```

Run unit tests:

```bash
ctest --preset linux-debug-test --output-on-failure --label-regex "cpp|python-unit"
```

If the change touches the API -> gRPC path, prompt formatting, or response mapping, also run the focused contract gate:

如果修改涉及 API -> gRPC 路径、prompt 格式化、或响应映射，请额外运行下面这条契约测试：

```bash
python3 -m pytest -q tests/python/test_grpc_contracts.py
```

## Prompt Trace Verification

When you need to prove that a WebUI or gateway prompt reached `compute_server`
intact and the computed result returned through `api_server`, enable the trace
switch on both services:

```bash
export LARK_MEMORY_CORE_DEBUG_PROMPT_IO=1
```

Use a unique sentinel prompt such as `TRACE_SENTINEL_<timestamp>_<random>`, then
verify the same `request_id` appears in both logs:

```bash
grep 'API server received prompt' <api-log>
grep 'API server returning result' <api-log>
grep 'Compute server received prompt' <compute-log>
grep 'Compute server returning result' <compute-log>
```

Minimum verification expectations:

1. The API log shows the rendered prompt and `prompt_chars`.
2. The Compute log shows the prompt it actually received over gRPC.
3. The API and Compute result logs share the same `request_id`.
4. The HTTP response body matches the result logged by `api_server`.

## Real Integration and Benchmark

When the change touches the real serving path or real model integration:

```bash
export REAL_INTEGRATION_MODEL="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
export REAL_DATASET_PATH="/path/to/real_dataset.jsonl"
export REAL_INTEGRATION_API_KEY="<client-api-key>"

python3 -m pytest -q -m real_integration tests/integration_real
python3 benchmarks/real_inference_benchmark.py
```

These paths require a running service, a real model backend, and a real dataset.

## Pull Request Checklist

1. Run the root CMake configure step.
2. Build `generate_python_proto`, `compute_server`, and `compute_server_tests`.
3. Run `ctest --preset ... --label-regex "cpp|python-unit"`.
4. If the change touches the API -> gRPC path, run `python3 -m pytest -q tests/python/test_grpc_contracts.py`.
5. Update docs and examples when public behavior changes.
6. Add or update tests for the change.
7. Keep Compute Server as the single source of truth for model metadata and visibility.
8. Do not reintroduce Docker, cluster, or macOS-specific contribution paths unless support policy changes explicitly.
