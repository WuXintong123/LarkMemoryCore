# OpenClaw Feishu + Ruyi Serving Runbook

## 1. 目标

这份 runbook 用于 `buddy-ascend` 上的隔离 OpenClaw + Feishu + Ruyi Serving 实机联调。

竞赛交付版本统一使用 `./ops/feishu_office_competition_preflight.sh`、
`./ops/feishu_office_competition_start.sh` 和
`./ops/feishu_office_competition_stop.sh` 管理运行时，而不是切回
`~/ruyi-serving` 那套默认 `systemd --user` 部署。

本次唯一行为基线是：

- 对外只继续使用 `POST /v1/chat/completions`
- API trace 完整记录 raw request
- compute prompt 只保留最后一条 `user`
- 不新增 Feishu 专用 endpoint

## 2. 联调前准备

1. 在代码仓执行自动化放行序列：

```bash
cmake --preset linux-debug
cmake --build --preset linux-debug-build --target generate_python_proto compute_server compute_server_tests
ctest --preset linux-debug-test --output-on-failure --label-regex "cpp|python-unit"
python3 -m pytest -q tests/python
python3 -m pytest -q tests/python/test_production_behaviors.py -k "openclaw or feishu or latest_user or debug_trace"
python3 -m pytest -q tests/python/test_grpc_contracts.py
```

2. 在 `buddy-ascend` 上启动隔离实例，建议端口固定为：
   - API: `127.0.0.1:18100`
   - compute: `0.0.0.0:19100`
   - tuned daemon: `127.0.0.1:19600`
3. API 和 compute 进程都导出 `RUYI_DEBUG_PROMPT_IO=1`。
4. 隔离环境显式设置 `CLUSTER_CONFIG_FILE=""`。
5. 确认真实模型 `serving` 配置已经包含：
   - `api_mode = both`
   - `prompt_style` 与真实模型匹配；`buddy-ascend` 上的 DeepSeek R1 样例使用 `buddy_deepseek_r1`
   - `default_max_tokens = 64`
   - `max_max_tokens = 256`
   - `request_timeout_ms = 30000`
   - `stream_idle_timeout_s = 30`
   - `max_input_chars` 为真实限制值

## 3. OpenClaw / Feishu 配置

1. 使用 `examples/openclaw_config.jsonc` 作为 provider 样例。
2. `baseUrl` 固定写成 `http://127.0.0.1:18100/v1`。
3. OpenClaw Feishu channel 继续走已有 OpenAI provider，不新增 Feishu 专用 endpoint。
4. 竞赛运行时默认从 `~/.openclaw/.env` 读取 `RUYI_API_KEY` 与
   `OPENCLAW_GATEWAY_TOKEN`，并由 `ops/feishu_office_competition_start.sh`
   自动重启 gateway。
5. 如果使用 OpenClaw `2026.4.2` 验收流式场景，保留样例里的 provider 别名 `ruyi_stream`。
   已知现象：该版本会把 provider 名称等于 `ruyi` 的 openai-completions 模型强制降成非流式。
6. 设置 `RUYI_API_KEY`，确保与隔离实例一致。
7. 重启 OpenClaw Gateway：

```bash
openclaw gateway restart
openclaw models list
```

## 4. 执行顺序

1. 先运行服务侧检查脚本：

```bash
cd /home/huangyiheng/src/ruyi-serving-feishu-live-20260416
export RUYI_FEISHU_API_LOG_PATH=.run/feishu-office-competition/logs/api.log
export RUYI_FEISHU_COMPUTE_LOG_PATH=.run/feishu-office-competition/logs/compute.log
./ops/openclaw_feishu_buddy_ascend_check.sh \
  --scenario dm-nonstream \
  --trace-token DM-NS-1-20260418-A \
  --trace-token DM-NS-2-20260418-B
```

2. 按脚本提示，在 Feishu 中人工发送两轮文本消息。
   竞赛版本目前仍保留这一步人工动作；脚本负责服务检查、日志采集与证据归档。
   对流式场景，必须先等第二轮回复在 Feishu 里完全结束，再按回车继续脚本。
   如果过早回车，第一次核验可能会因为日志尚未完全落盘而显示失败。
3. 场景顺序固定为：
   - DM + 非流式
   - DM + 流式
   - 群聊 `@bot` + 非流式
   - 群聊 `@bot` + 流式
4. 每个场景都换两条新的 trace token，避免日志串场。
5. 开始流式场景前，确认 OpenClaw 当前主模型仍指向 `ruyi_stream/...`。
6. 对流式场景，按回车前可以先确认：

```bash
curl -fsS -H "Authorization: Bearer $(cat /home/huangyiheng/src/ruyi-serving-feishu-live-20260416/.run/feishu-office-competition/runtime/api_key.txt)" \
  http://127.0.0.1:18100/v1/admin/metrics
```

只有当 `active_compute_slots=0` 且 `queued_requests=0` 时，才继续跑核验。

## 5. 预期现象

- OpenClaw / Feishu 侧回复成功，无 4xx / 5xx
- 第二轮 raw request 能看到第一轮历史
- API 最终 prompt 只含第二轮最新 `user`
- compute 收到的 prompt 与 API 最终 prompt 一致
- 流式场景响应类型是 `text/event-stream`
- 所有响应都带 `X-Request-Id`

## 6. 失败定位

1. 如果 OpenClaw / Feishu 没有回复：
   - 先看 `openclaw gateway` 是否已重启
   - 再看 API `/ready` 和 `/v1/models`
2. 如果出现 `422` 或 `messages.*.content` 类型错误：
   - 优先核对 API 是否已经部署到包含本 PR 的版本
3. 如果第二轮 raw request 有历史，但 compute prompt 也带了历史：
   - 直接判定失败
4. 如果流式返回不是 `text/event-stream`：
   - 直接判定失败
5. 如果日志缺少 `API server received raw request` 或 `Compute server received prompt`：
   - 先确认两个进程都打开了 `RUYI_DEBUG_PROMPT_IO=1`
   - 再确认脚本读取的是本次隔离实例的日志
6. 如果 OpenClaw 已配置流式，但 API raw request 里仍然是 `stream:false`：
   - 优先检查 provider 名称是不是仍然是 `ruyi`
   - 改用 `ruyi_stream` 后再重试
7. 如果流式场景第一次核验失败，但 OpenClaw 会话和 API/compute 日志里已经能看到相同 trace token：
   - 先等待流式第二轮完全结束
   - 再用相同 scenario 和相同两条 trace token 重跑一次核验脚本
