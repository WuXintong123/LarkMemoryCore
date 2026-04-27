# OpenClaw Feishu 日志核对说明

## 1. 必看日志

1. `API server received raw request`
   - 关键字段：`request_id`、`request_kind`、`raw_request`
   - 第二轮必须能在 `raw_request` 里同时看到第一轮和第二轮 trace token
2. `API server received prompt`
   - 关键字段：`request_id`、`prompt`
   - `prompt` 里只能出现第二轮最新 `user`
3. `Compute server received prompt`
   - 关键字段：`request_id`、`prompt`
   - 必须与 API 的最终 `prompt` 完全一致
4. `Request completed`
   - 关键字段：`request_id`、`status_code`
   - 期望是 `200`

## 2. 如何判断“历史只用于 trace，不进入 compute”

按同一个 `request_id` 对齐三条日志：

1. `API server received raw request`
   - 看到 `round1-token` 和 `round2-token`
2. `API server received prompt`
   - 只能看到 `round2-token`
3. `Compute server received prompt`
   - 与 API prompt 完全相同，也只能看到 `round2-token`

只要 `API prompt` 或 `compute prompt` 里还能看到 `round1-token`，就判定失败。

## 3. 流式场景补充检查

- HTTP 响应头要有 `content-type: text/event-stream`
- API 日志应出现 `API server returning streaming result` 或稳定的 streaming error 事件
- 不能出现 SSE 格式截断、半条 JSON 或无 `[DONE]` 的成功流

## 4. 常见失败信号

- `status_code=422`
- `messages.*.content`
- `Only text content parts are supported` 以外的隐式降级
- `API server received raw request` 缺失
- `Compute server received prompt` 与 API prompt 不一致
