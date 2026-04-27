# OpenClaw Feishu Verification Note 2026-04-13

Status: Completed on `buddy-ascend` isolated validation instance.

- Date: 2026-04-13
- Host: buddy-ascend
- Repo revision: workspace snapshot based on `fc8d87844f2654a46fa12c57e24c897d09dfc20e`
- API port: 48100
- Compute port: 49100
- OpenClaw profile: `trace`
- Feishu app: `Ruyi Test Bot` (`cli_a95e2f98f3b81ccb`)
- RUYI_DEBUG_PROMPT_IO: enabled on API and compute

Known validation note:
OpenClaw `2026.4.2` hard-codes `provider === "ruyi"` to non-streaming in its
`openai-completions` provider. Streaming validation on `buddy-ascend` used a
provider alias `ruyi_stream` in `~/.openclaw/openclaw.json`.

## Scenario Summary

| Scenario | Trace token round 1 | Trace token round 2 | Result |
| --- | --- | --- | --- |
| DM + non-stream | `DM-NS-1-20260413-B` | `DM-NS-2-20260413-B` | pass |
| DM + stream | `DM-S-1-20260413-D` | `DM-S-2-20260413-D` | pass |
| Group `@bot` + non-stream | `GROUP-NS-1-20260413-B` | `@Ruyi Test Bot GROUP-NS-2-20260413-C` | pass |
| Group `@bot` + stream | `@Ruyi Test Bot GROUP-S-1-20260413-A` | `@Ruyi Test Bot GROUP-S-2-20260413-B` | pass |

## Mandatory Evidence Per Scenario

- DM + non-stream:
  - `API server received raw request`: `7dd33969-f065-4670-a48d-2a1445e7250c`
  - `API server received prompt`: `7dd33969-f065-4670-a48d-2a1445e7250c`
  - `Compute server received prompt`: `7dd33969-f065-4670-a48d-2a1445e7250c`
  - `X-Request-Id`: `7dd33969-f065-4670-a48d-2a1445e7250c`
  - Result summary: second-round raw request carried first-round history; API and compute prompt kept only latest `user`.
- DM + stream:
  - `API server received raw request`: `360e5c93-b290-460a-be18-4e7e5ad313d9`
  - `API server received prompt`: `360e5c93-b290-460a-be18-4e7e5ad313d9`
  - `Compute server received prompt`: `360e5c93-b290-460a-be18-4e7e5ad313d9`
  - `X-Request-Id`: `360e5c93-b290-460a-be18-4e7e5ad313d9`
  - `content-type`: `text/event-stream; charset=utf-8`
  - Result summary: OpenClaw DM streaming reached Ruyi with `stream:true`; second-round raw request contained first-round history.
- Group `@bot` + non-stream:
  - `API server received raw request`: `23c7c32a-bbb6-43ea-a2ca-be1336b0e460`
  - `API server received prompt`: `23c7c32a-bbb6-43ea-a2ca-be1336b0e460`
  - `Compute server received prompt`: `23c7c32a-bbb6-43ea-a2ca-be1336b0e460`
  - `X-Request-Id`: `23c7c32a-bbb6-43ea-a2ca-be1336b0e460`
  - Result summary: group session metadata reported `is_group_chat=true` and `was_mentioned=true`; second-round raw request carried prior group history and latest `user` only reached compute.
- Group `@bot` + stream:
  - `API server received raw request`: `fb9a372d-3faf-4233-aa13-8e063415af69`
  - `API server received prompt`: `fb9a372d-3faf-4233-aa13-8e063415af69`
  - `Compute server received prompt`: `fb9a372d-3faf-4233-aa13-8e063415af69`
  - `X-Request-Id`: `fb9a372d-3faf-4233-aa13-8e063415af69`
  - `content-type`: `text/event-stream; charset=utf-8`
  - Result summary: group streaming required the `ruyi_stream` provider alias; after switching, second-round raw request arrived with `stream:true` and compute saw only the latest `user`.

## Final Release Decision

- Overall result: pass
- Blocking issue: none in `ruyi-serving`; external validation note recorded for OpenClaw `provider=ruyi` non-streaming behavior
- Follow-up owner: OpenClaw upstream for provider-name-specific streaming workaround removal
