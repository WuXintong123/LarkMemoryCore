# LarkMemoryCore

OpenAI API-compatible serving built with FastAPI + gRPC.

LarkMemoryCore 是一个面向飞书项目决策记忆和真实模型后端的 OpenAI API 兼容推理服务，采用 FastAPI + gRPC 双进程架构。

Commercial identity for the RuyiAI-Stack launch:

- Organization: `RuyiAI-Stack`
- Public site: `https://ruyiai-stack.github.io`
- Public repository: `RuyiAI-Stack/ruyiai-stack.github.io`

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
- `./ops/feishu_office_competition_start_kimi.sh` — slim launcher when only
  `moonshot/kimi-k2.5` is needed (skips the HuggingFace adapter daemon).

Exact end-to-end reproduction:

- `docs/competition-feishu-office-reproduction.md`

## Feishu Chat Bridge

`ops/feishu_bridge.py` is a long-connection (WebSocket) bridge that lets a
Feishu / Lark bot talk to LarkMemoryCore. It receives `im.message.receive_v1`
events from the Feishu Open Platform via `lark-oapi`, calls
`/v1/chat/completions` with project-memory metadata, and replies in the same
chat. No public webhook URL is required.

### Architecture

```
Feishu user ──(WS)──▶ feishu_bridge.py ──(HTTP)──▶ LarkMemoryCore /v1/chat/completions
                            │                              │
                            │                              └─▶ moonshot/kimi-k2.5
                            │                                  via ops/openclaw_kimi_cli.py
                            └─(reply)── Feishu im.v1.message.reply
```

### Behaviour

- **Single chat (`p2p`)**: every message is treated as a conversation with
  the bot and gets a reply.
- **Group chat**: the bridge inspects `message.mentions[*].id.open_id` and
  only replies when the bot's own `open_id` is mentioned. Non-mention
  messages are still recorded with an `observe-only` log line, which is
  useful when the app is granted `im:message.group_msg(:readonly)` so the
  bot needs to read every message but only respond when explicitly addressed.
- Messages are processed on a 4-thread worker pool so the WebSocket handler
  acks each event in milliseconds. A 10-minute / 500-entry sliding-window
  dedup map drops Feishu retries with the same `message_id`.

### Required Feishu permissions

Apply on the Feishu Open Platform under **Permission Management**, then
**publish a new app version** (permissions do not take effect until a
version is approved):

| Permission | Purpose |
| --- | --- |
| `im:message` | Base IM capability |
| `im:message:send_as_bot` | Reply as the bot |
| `im:message.group_at_msg` (+ `:readonly`) | Receive `@bot` events in groups |
| `im:message.p2p_msg` (+ `:readonly`) | Receive 1:1 messages |
| `im:message.group_msg.readonly` | (Optional) read every group message; required only if you want the bridge to log non-`@` messages too |

Then under **Event Subscription** select **Use long connection to receive
events** and add the `im.message.receive_v1` event.

### Configuration

Copy the template and fill in real credentials:

```bash
cp ops/feishu_bridge.env.example ops/feishu_bridge.env
chmod 600 ops/feishu_bridge.env
```

Required fields:

| Variable | Description |
| --- | --- |
| `FEISHU_APP_ID`, `FEISHU_APP_SECRET` | App credentials from the Open Platform. |
| `LARK_MEMORY_CORE_API_KEY` | Same key used by the API server (`.run/feishu-office-competition/runtime/api_key.txt`). |
| `LARK_MEMORY_CORE_BASE_URL` | Default `http://127.0.0.1:18100`. |
| `LARK_MEMORY_CORE_MODEL` | Default `moonshot/kimi-k2.5`. |
| `LARK_MEMORY_CORE_TENANT_ID`, `..._PROJECT_ID` | Memory scope; must match the events injected via `seed_memory_engine` for hits to count. |
| `FEISHU_BOT_OPEN_ID` | Bot `open_id`, used to detect `@bot` in groups. Look up via `GET https://open.feishu.cn/open-apis/bot/v3/info`. |
| `FEISHU_VERIFY_TOKEN`, `FEISHU_ENCRYPT_KEY` | Only needed if enabled in the event subscription page. |

`ops/feishu_bridge.env` is gitignored — never commit real secrets. The
template `ops/feishu_bridge.env.example` is the canonical reference.

### Install dependencies

The bridge needs `lark-oapi` and `requests` in the runtime Python
environment:

```bash
python -m pip install lark-oapi requests
```

### Run

In one shell, bring up LarkMemoryCore (slim, kimi-only path is enough for
the bridge):

```bash
bash ops/feishu_office_competition_start_kimi.sh
curl -sS http://127.0.0.1:18100/ready
```

In a second shell, start the bridge:

```bash
set -a; source ops/feishu_bridge.env; set +a
python ops/feishu_bridge.py
```

Logs stream to stdout and to
`.run/feishu-office-competition/logs/feishu_bridge.log`. Background usage:

```bash
nohup bash -lc 'set -a; source ops/feishu_bridge.env; set +a; \
  exec python ops/feishu_bridge.py' \
  >> .run/feishu-office-competition/logs/feishu_bridge.stdout.log 2>&1 < /dev/null &
disown
```

### Verify

`@`-mention the bot in a Feishu group (or DM it) with any prompt; expect
three log lines:

```
INFO received chat_type=group mentioned=True chat_id=oc_... message_id=om_... chars=N preview=...
INFO memory_hits=K latency=Xs prompt_chars=N reply_chars=M
INFO reply ok message_id=om_...
```

A non-mention group message logs `mentioned=False` followed by
`observe-only ... (no reply, not mentioned)` and triggers no LLM call.

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
- `GET /v1/competition/feishu-office/evidence`

Competition demo helpers:

```bash
python3 -m competition.feishu_office.seed_memory_engine \
  --base-url http://127.0.0.1:18100 \
  --api-key "$LARK_MEMORY_CORE_API_KEY"
```

The evidence endpoint summarizes real dataset, evaluation, and Feishu
acceptance artifacts. It intentionally omits model answer bodies.

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
cd /home/huangyiheng/src/ruyi-serving-feishu-live-20260416
export REAL_INTEGRATION_MODEL="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
export REAL_DATASET_PATH="/home/huangyiheng/src/ruyi-serving-feishu-live-20260416/tests/real_data/huangyiheng_2026_02_real.jsonl"
export REAL_INTEGRATION_BASE_URL="http://127.0.0.1:18100"
export REAL_INTEGRATION_API_KEY="$(cat /home/huangyiheng/src/ruyi-serving-feishu-live-20260416/.run/feishu-office-competition/runtime/api_key.txt)"
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
