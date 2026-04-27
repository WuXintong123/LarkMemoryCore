# Memory Engine Benchmark Report

## Scope

本报告说明方向 B 项目决策记忆的自证评测方式。评测使用仓库已有真实材料：

- `docs/openclaw-feishu-runbook.md`
- `ops/feishu_office_competition_common.sh`
- `competition/feishu_office/data/test.jsonl`
- `reports/openclaw-feishu-20260418-*`

测试入口：

```bash
python3 -m pytest -q tests/python/test_memory_engine.py
```

## 抗干扰测试

目标：在大量无关真实办公材料注入后，一周前的关键项目记忆仍能被检索到。

测试过程：

1. 从 `docs/openclaw-feishu-runbook.md` 读取真实决策：
   - 竞赛交付版本统一使用 `ops/feishu_office_competition_preflight.sh`
   - 使用 `ops/feishu_office_competition_start.sh`
   - 使用 `ops/feishu_office_competition_stop.sh`
   - 不切回默认 `systemd --user` 部署
2. 用真实 OpenClaw/飞书 envelope 格式包装该文本并注入记忆库。
3. 从 `competition/feishu_office/data/test.jsonl` 注入 40 条真实 held-out 办公语料作为干扰。
4. 将查询时间推进到 `2026-04-25T10:00:00+08:00`。
5. 查询“竞赛运行时不用 legacy systemd 时应该使用哪些脚本？”。

验收指标：

- `hit_at_1 = 1`
- 第一条结果为 `active`
- 来源为 `repo://docs/openclaw-feishu-runbook.md`
- 决策内容包含 `feishu_office_competition_start.sh` 和 `systemd --user`

## 矛盾更新测试

目标：先后输入冲突配置，系统按时间和主题覆盖旧版本，只返回最新 active 决策。

测试过程：

1. 从 `docs/openclaw-feishu-runbook.md` 读取旧运行时建议：
   - `request_timeout_ms = 30000`
2. 从 `ops/feishu_office_competition_common.sh` 读取实际竞赛运行时配置：
   - `"request_timeout_ms": 300000`
3. 两条事件使用同一租户、项目、会话和主题 `request_timeout_ms`。
4. 第二条事件时间晚于第一条事件。
5. 查询“竞赛运行时 request_timeout_ms 使用多少？”。

验收指标：

- 最新结果来源为 `repo://ops/feishu_office_competition_common.sh`
- 最新结果包含 `300000`
- 最新结果 `version = 2`
- 旧版本状态为 `superseded`
- `/v1/memory/report` 中 `version_correctness = 1.0`

## 效能指标验证

目标：证明短问题可以通过记忆补全历史上下文，减少用户重复输入。

测试过程：

1. 注入 `docs/openclaw-feishu-runbook.md` 中“本次唯一行为基线”真实材料。
2. 用户只输入短问题：“基线是什么？”
3. Retriever 命中历史决策卡。
4. Prompt Composer 自动补齐历史决策卡片。

验收指标：

- 组合 prompt 包含“历史决策卡片”
- 组合 prompt 包含 `POST /v1/chat/completions`
- `hit_count = 1`
- `saved_characters > 0`
- `efficiency_gain_ratio > 0.5`

## API 验证

测试覆盖以下生产接口：

- `POST /v1/memory/events`
- `GET /v1/memory/search`
- `GET /v1/memory/report`

接口复用现有 API key 鉴权，认证启用时要求 `admin` scope。认证关闭的本地单测环境中，接口直接执行以验证行为。

## 运行结果记录

本地实现完成后执行：

```bash
python3 -m pytest -q tests/python/test_memory_engine.py
```

当前结果：

```text
4 passed
```

完整回归仍需按 `docs/validation-gates.md` 执行。涉及真实模型和实机飞书验收的命令只应在 `buddy-ascend` 隔离运行时上执行。
