# OpenClaw Feishu Verification Note 2026-04-18

Status: Completed on the competition runtime with Feishu operator messaging.

- Date: 2026-04-18
- Host: buddy-ascend
- Repo worktree: `/home/huangyiheng/src/ruyi-serving-feishu-live-20260416`
- Active branch: `fix/openclaw-real-user-question`
- Competition runtime:
  - API: `127.0.0.1:18100`
  - compute: `0.0.0.0:19100`
  - tuned daemon: `127.0.0.1:19600`
- Tuned model id: `ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice`
- `RUYI_DEBUG_PROMPT_IO`: enabled on API and compute

## Completed Checks

- Competition dataset built and validated
- QLoRA adapter trained on the server GPU
- Competition runtime start / stop / preflight scripts passed
- `/health` passed
- `/v1/models` listed both baseline and tuned models
- baseline real integration passed (`2 passed in 224.49s`)
- tuned real integration passed (`2 passed in 26.21s`)
- small-sample benchmark executed for both models
- OpenClaw CLI path resolved and gateway restart path verified
- four Feishu acceptance scenarios completed with PASS:
  - DM + non-stream
  - DM + stream
  - Group `@bot` + non-stream
  - Group `@bot` + stream

## Feishu Acceptance Evidence

| Scenario | Request ID | Result |
| --- | --- | --- |
| DM + non-stream | `d82be4ff-74c3-4a5a-837c-eeafaeb3fbcc` | PASS |
| DM + stream | `4e8ce165-bf46-47b2-863e-25bdaba9b88a` | PASS |
| Group `@bot` + non-stream | `4c4a245e-43a1-48b7-9d36-c82df463df90` | PASS |
| Group `@bot` + stream | `7c04f1bb-5abe-45d8-94e1-99f088f0e4a4` | PASS |

The runtime and log paths are ready for the operator:

- API log: `.run/feishu-office-competition/logs/api.log`
- compute log: `.run/feishu-office-competition/logs/compute.log`
- daemon log: `.run/feishu-office-competition/logs/daemon.log`

The existing verification script should be run with:

```bash
cd /home/huangyiheng/src/ruyi-serving-feishu-live-20260416
export RUYI_FEISHU_API_LOG_PATH=.run/feishu-office-competition/logs/api.log
export RUYI_FEISHU_COMPUTE_LOG_PATH=.run/feishu-office-competition/logs/compute.log
./ops/openclaw_feishu_buddy_ascend_check.sh --scenario dm-nonstream --trace-token DM-NS-1-20260418-A --trace-token DM-NS-2-20260418-B
```

Generated report directories:

- `reports/openclaw-feishu-20260418-045903-dm-nonstream`
- `reports/openclaw-feishu-20260418-052933-dm-stream`
- `reports/openclaw-feishu-20260418-085833-group-at-nonstream`
- `reports/openclaw-feishu-20260418-091110-group-at-stream`

## Automation Note

The operator send step still required a human because OpenClaw `2026.4.2` does
not expose a Feishu inbound-message simulation command. Everything around that
step was executed and archived on the server.

For the exact step-by-step reproduction flow, use
`docs/competition-feishu-office-reproduction.md`.
