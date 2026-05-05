# LarkMemoryCore 使用指南

LarkMemoryCore 是一个 OpenAI API 兼容服务，同时提供企业级项目决策记忆能力。日常使用时，可以把它分成两层：

- 推理层：通过 `/v1/chat/completions`、`/v1/completions` 调用模型。
- 记忆层：通过 `/v1/memory/events`、`/v1/memory/search`、`/v1/memory/report` 写入、检索和验证项目决策记忆。

## 1. 启动服务

进入仓库并激活运行环境：

```bash
cd ~/LarkMemoryCore
conda activate ruyi-dev
```

执行竞赛运行时检查与启动：

```bash
./ops/feishu_office_competition_preflight.sh
./ops/feishu_office_competition_start.sh
```

启动后默认使用以下端口：

```text
API:     http://127.0.0.1:18100
compute: 127.0.0.1:19100
```

运行时 API key 会写入：

```text
.run/feishu-office-competition/runtime/api_key.txt
```

加载 API key：

```bash
export LARK_MEMORY_CORE_API_KEY="$(cat .run/feishu-office-competition/runtime/api_key.txt)"
```

## 2. 检查服务状态

健康检查：

```bash
curl http://127.0.0.1:18100/health
```

预期结果：

```json
{"status":"healthy"}
```

就绪检查：

```bash
curl -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" \
  http://127.0.0.1:18100/ready
```

预期结果：

```json
{
  "status": "ready",
  "ready_models": [
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
    "ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice"
  ]
}
```

查看模型：

```bash
curl -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" \
  http://127.0.0.1:18100/v1/models
```

## 3. 注入真实记忆数据

使用仓库真实文档、运行脚本和 held-out 数据集注入记忆事件：

```bash
python -m competition.feishu_office.seed_memory_engine \
  --base-url http://127.0.0.1:18100 \
  --api-key "$LARK_MEMORY_CORE_API_KEY"
```

该脚本会写入：

- 真实项目运行决策
- `request_timeout_ms` 的冲突更新记录
- 来自真实测试集的干扰语料

脚本可重复运行。重复事件会被事件 hash 去重，不会制造错误版本。

## 4. 查看记忆状态

```bash
curl -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" \
  http://127.0.0.1:18100/v1/memory/report
```

预期关键指标：

```json
{
  "enabled": true,
  "event_count": 44,
  "active_memory_count": 18,
  "superseded_memory_count": 1,
  "version_correctness": 1.0
}
```

指标含义：

- `event_count`：进入记忆引擎的真实事件数。
- `active_memory_count`：当前有效的结构化决策卡数量。
- `superseded_memory_count`：被新版本覆盖的旧决策数量。
- `version_correctness`：每个记忆主题是否只保留最新版本为 active。

## 5. 检索结构化决策卡

查询 `request_timeout_ms` 的当前决策：

```bash
curl -G \
  -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" \
  --data-urlencode "tenant_id=tenant-real" \
  --data-urlencode "project_id=feishu-office" \
  --data-urlencode "conversation_id=oc_group_trace_room" \
  --data-urlencode "query=竞赛运行时 request_timeout_ms 使用多少？" \
  http://127.0.0.1:18100/v1/memory/search
```

预期结果会返回 `cards`，每张卡包含：

```json
{
  "topic": "request_timeout_ms",
  "decision": "...300000...",
  "status": "active",
  "version": 2,
  "source_url": "repo://ops/feishu_office_competition_common.sh"
}
```

这里返回的是结构化决策卡，不是模型回答正文。

## 6. 调用模型

普通 chat 调用：

```bash
curl -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:18100/v1/chat/completions \
  -d '{
    "model": "ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice",
    "messages": [
      {"role": "user", "content": "竞赛运行时 request_timeout_ms 使用多少？"}
    ],
    "max_tokens": 128,
    "temperature": 0
  }'
```

如果记忆引擎已开启，API server 会在推理前完成以下流程：

1. 从当前请求中提取用户问题。
2. 按 tenant、project、conversation 检索 active 决策卡。
3. 将命中的历史决策卡注入本次 prompt。
4. 调用后端真实模型生成结果。

响应头会带上记忆命中信息：

```text
X-LarkMemoryCore-Memory-Hit-Count
X-LarkMemoryCore-Memory-Ids
```

## 7. 查看交付证据

证据接口用于读取真实数据集、真实评测结果和飞书实机验收摘要：

```bash
curl -H "Authorization: Bearer $LARK_MEMORY_CORE_API_KEY" \
  http://127.0.0.1:18100/v1/competition/feishu-office/evidence
```

该接口返回：

- dataset manifest
- quality report
- baseline / tuned 评测指标
- 飞书四场景实机验收通过记录

该接口不会返回模型回答正文。

## 8. 打开演示控制台

```bash
cd competition/feishu_office/demo_console
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

控制台用于录屏展示：

- 真实数据行数
- active / superseded 记忆数量
- 版本正确率
- 抗干扰检索
- 矛盾更新
- 调优评测指标
- 飞书四场景验收结果

控制台只展示真实指标和结构化决策卡，不展示模型回答正文。

## 9. 结构化决策卡存储位置

结构化决策卡存储在 SQLite 数据库中，不是单独的 JSON 或 Markdown 文件。

竞赛运行时路径：

```text
.run/feishu-office-competition/memory/decision_memory.sqlite3
```

核心表：

- `memory_events`：原始事件、清洗文本、metadata、去重 hash。
- `decision_memories`：长期结构化决策卡。
- `decision_memory_fts`：active 决策卡全文索引。
- `retrieval_logs`：检索命中、延迟、prompt 注入记录。

查看当前决策卡：

```bash
sqlite3 .run/feishu-office-competition/memory/decision_memory.sqlite3 \
  'select topic, status, version, source_url from decision_memories order by occurred_at desc;'
```

## 10. 停止服务

```bash
./ops/feishu_office_competition_stop.sh
```

## 11. 常见判断

如果 `/health` 是 healthy、`/ready` 是 ready、`/v1/models` 能返回模型列表，说明服务链路已启动。

如果 `/v1/memory/report` 中 `enabled=true` 且 `version_correctness=1.0`，说明记忆引擎已开启并且版本覆盖逻辑正常。

如果 `/v1/memory/search` 能返回 `status=active` 的卡片，说明结构化决策检索正常。

如果 `/v1/chat/completions` 响应头中 `X-LarkMemoryCore-Memory-Hit-Count` 大于 0，说明模型调用前已经注入历史决策记忆。
