# LarkMemoryCore

OpenAI API-compatible serving built with FastAPI + gRPC.

LarkMemoryCore 是一个面向飞书项目决策记忆和真实模型后端的 OpenAI API 兼容推理服务，采用 FastAPI + gRPC 双进程架构。

## What This Repo Expects

- the official build and test entrypoint is root `CMake + Ninja + CTest`
- Python API unit tests are part of the default CTest graph
- real model validation stays explicit and is **not** hidden behind unit-test targets
- deployment scripts assume a target Linux host with `systemd --user`

If you are changing this repo, expect to run explicit configure, proto generation, build, unit tests, and real-model validation steps.

如果你要修改这个仓库，默认就应该显式执行 configure、protobuf 生成、构建、单元测试，以及真实模型验证，而不是依赖一条“自动到底”的命令。

## Repository Layout

- `compute_server/`: C++ gRPC backend
- `api_server/`: FastAPI API layer
- `proto/`: shared protobuf schema
- `api_server/proto/`: generated Python protobuf stubs
- `tests/python/`: API unit and contract tests
- `tests/integration_real/`: real-model integration tests
- `benchmarks/`: real-data benchmark entrypoints
- `examples/`: SDK and smoke examples
- `ops/`: deployment and host orchestration scripts
- `docs/`: deployment and validation documentation
- `competition/feishu_office/`: competition dataset, training, evaluation, and tuned-model runtime assets

## Competition Delivery

The Feishu Office Assistant competition delivery is implemented in
`competition/feishu_office/` and the companion docs:

- `docs/competition-feishu-office-dataset.md`
- `docs/competition-feishu-office-training-report.md`
- `docs/competition-feishu-office-effect-report.md`
- `docs/competition-feishu-office-demo.md`
- `docs/memory-definition-architecture-whitepaper.md`
- `docs/memory-benchmark-report.md`

Competition runtime helpers:

- `./ops/feishu_office_train_env.sh`
- `./ops/feishu_office_competition_preflight.sh`
- `./ops/feishu_office_competition_start.sh`
- `./ops/feishu_office_competition_stop.sh`

Exact end-to-end reproduction:

- `docs/competition-feishu-office-reproduction.md`

## Build Prerequisites

### Ubuntu / Debian

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
  nlohmann-json3-dev \
  python3 \
  python3-pip
```

Install Python dependencies with the same interpreter you will use for repo commands:

```bash
python3 -m pip install -r requirements-dev.txt
```

## Configure the Root Build Graph

Run all build commands from the repository root.

Linux:

```bash
cmake --preset linux-debug
```

CI uses:

```bash
cmake --preset ci-linux
```

Configure-time checks cover:

- Ninja
- `pkg-config`
- Python `>= 3.10`
- Python modules `grpc_tools`, `pytest`, `requests`
- C++ dependencies discovered by the compute subproject during the same configure pass

## Generate Python Protobuf Stubs

The API layer imports generated files from `api_server/proto/`, so Python protobuf generation is an explicit build target:

```bash
cmake --build --preset linux-debug-build --target generate_python_proto
```

This target runs the following underlying command:

```bash
python3 -m grpc_tools.protoc \
  -I proto \
  --python_out=api_server/proto \
  --grpc_python_out=api_server/proto \
  proto/compute.proto
```

Generated files:

- `api_server/proto/compute_pb2.py`
- `api_server/proto/compute_pb2_grpc.py`

## Build the Compute Server and C++ Tests

Build the service binary and C++ test executables from the root graph:

```bash
cmake --build --preset linux-debug-build --target compute_server compute_server_tests
```

Canonical runtime artifact:

```text
build/bin/compute_server
```

Run C++ and Python unit tests through CTest:

```bash
ctest --preset linux-debug-test --output-on-failure --label-regex "cpp|python-unit"
```

## Python API Unit Tests

`tests/python` mainly validates API behavior and contracts. These tests do **not** require a real model backend.

- request and response contracts
- auth and rate limiting behavior
- readiness and API-side routing behavior
- API -> gRPC prompt/response contract checks
- docs contract checks

If you are changing FastAPI handlers or API behavior, run this suite before starting the API server manually.

Direct path:

```bash
python3 -m pytest -q tests/python
```

## Local Runtime Configuration

For a local source-tree run, prepare the runtime files manually:

```bash
cp config.example.env .env
cp models.json.example models.json
```

Then replace every placeholder `tool.cli_path` with a real executable path.

If `.env` points `MODELS_CONFIG_FILE` to a custom location, that location becomes the active runtime config.

## Run the Service Locally

Build the required artifacts first:

```bash
cmake --build --preset linux-debug-build --target generate_python_proto compute_server
```

Start the compute server:

```bash
./build/bin/compute_server
```

Start the API server in a second shell:

```bash
python3 -m uvicorn api_server.main:app --host 127.0.0.1 --port 8000
```

Useful endpoints:

- `GET /health`
- `GET /ready`
- `GET /v1/models`
- `GET /v1/models/{model_id}`
- `GET /metrics`
- `GET /v1/admin/backends`
- `POST /v1/admin/reload-models`
- `POST /v1/memory/events`
- `GET /v1/memory/search`
- `GET /v1/memory/report`

## gRPC Contract Rule

For models using `buddy_deepseek_r1`, the API layer does **not** send the raw chat JSON payload to the backend. It first renders a plain-text prompt and sends that prompt through gRPC.

That means direct CLI / direct gRPC / API result comparisons must use the same final prompt string. For example, chat input `hello!` is compared against backend prompt `User: hello!`, not against raw stdin `hello!`.

## Real Model Compiler Validation

Before treating `lark-memory-core` as validated against a real model backend, compile and direct-test the actual model CLI that the runtime will launch.

For the default Buddy DeepSeek R1 path:

```bash
cd /home/huangyiheng/buddy-mlir
ninja -C build buddy-deepseek-r1-cli
printf "Say READY only.\n" | ./build/bin/buddy-deepseek-r1-cli --max-tokens=4 --no-stats
```

Then ensure the active `models.json` points `tool.cli_path` at that executable.

## Real Integration and Benchmark Gates

These gates require:

- a running `lark-memory-core` instance
- a real model binary
- a real dataset
- a client API key if auth is enabled

Example:

```bash
cd /home/huangyiheng/src/lark-memory-core-feishu-live-20260416
export REAL_INTEGRATION_MODEL="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
export REAL_DATASET_PATH="/home/huangyiheng/src/lark-memory-core-feishu-live-20260416/tests/real_data/huangyiheng_2026_02_real.jsonl"
export REAL_INTEGRATION_BASE_URL="http://127.0.0.1:18100"
export REAL_INTEGRATION_API_KEY="$(cat /home/huangyiheng/src/lark-memory-core-feishu-live-20260416/.run/feishu-office-competition/runtime/api_key.txt)"
export REAL_INTEGRATION_MAX_SAMPLES=1
export REAL_INTEGRATION_TIMEOUT_S=180
```

Run the real integration tests:

```bash
pytest -q -m real_integration tests/integration_real
```

Run the real benchmark:

```bash
python3 benchmarks/real_inference_benchmark.py
```

These are explicit developer gates. They are not folded into the default unit-test graph because they depend on a live service, a real model, and a real dataset.

## CI Contract

CI uses the same root build graph as developers:

```bash
cmake --preset linux-debug
cmake --build --preset linux-debug-build --target generate_python_proto compute_server compute_server_tests
ctest --preset linux-debug-test --output-on-failure --label-regex "cpp|python-unit"
```

That keeps configure, build, and unit-test behavior aligned across local development and automation.

## Additional Examples

- [`examples/openai_sdk_compat.py`](examples/openai_sdk_compat.py)
- [`examples/javascript/openai_sdk_compat.mjs`](examples/javascript/openai_sdk_compat.mjs)
- [`examples/postman/lark-memory-core.postman_collection.json`](examples/postman/lark-memory-core.postman_collection.json)

## Contributing, Security, and License

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Security Policy: [SECURITY.md](SECURITY.md)
- License: Apache-2.0
