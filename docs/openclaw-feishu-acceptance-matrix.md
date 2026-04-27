# OpenClaw Feishu Acceptance Matrix

## 正向场景

| 场景 | 轮次 | 期望 |
| --- | --- | --- |
| DM + 非流式 | 单轮 | 回复成功，状态 200，`X-Request-Id` 存在 |
| DM + 非流式 | 多轮 | 第二轮 raw request 含第一轮历史，compute prompt 只含最新 `user` |
| DM + 流式 | 单轮 | `content-type` 为 `text/event-stream`，SSE 事件格式正确 |
| DM + 流式 | 多轮 | 第二轮 raw request 含第一轮历史，API prompt 与 compute prompt 完全一致 |
| 群聊 `@bot` + 非流式 | 单轮 | 回复成功，状态 200，未出现 422 |
| 群聊 `@bot` + 非流式 | 多轮 | 第二轮 raw request 含第一轮历史，compute prompt 只含最新 `@bot` 文本 |
| 群聊 `@bot` + 流式 | 单轮 | `text/event-stream` 正常返回，SSE 不截断 |
| 群聊 `@bot` + 流式 | 多轮 | 最新 `user` 生效，历史只保留在 raw request trace |

## 负向场景

| 场景 | 输入 | 期望 |
| --- | --- | --- |
| 非文本内容 | `messages[].content` 含图片或文件片段 | 返回稳定 `400`，错误类型为 `invalid_request_error`，并包含 `Only text content parts are supported` |
| OpenClaw / Feishu 附加字段 | 顶层 metadata、channel、conversation 字段 | 不触发 `422` |
| 尾部 assistant placeholder | 最后一条是空 `assistant` | 请求仍可处理，compute prompt 继续只取最后一条 `user` |
| 流式异常 | 上游中断或 backend error | SSE 仍返回稳定 `error` 事件结构 |

## 主场景放行门禁

以下 4 组场景全部通过后，才允许判定 `buddy-ascend` 实机验收通过：

1. DM + 非流式，两轮
2. DM + 流式，两轮
3. 群聊 `@bot` + 非流式，两轮
4. 群聊 `@bot` + 流式，两轮
