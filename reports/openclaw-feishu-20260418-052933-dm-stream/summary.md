# OpenClaw Feishu Check Summary: dm-stream

## Host Info

```text
date=2026-04-18 05:29:33 UTC
host=plct-gpu
user=huangyiheng
cwd=/home/huangyiheng/src/ruyi-serving-feishu-live-20260416
repo_root=/home/huangyiheng/src/ruyi-serving-feishu-live-20260416
scenario=dm-stream
trace_token_round_1=DM-S-1-20260418-A
trace_token_round_2=DM-S-2-20260418-B
api_base_url=http://127.0.0.1:18100
log_since=15 minutes ago
git_head=2801e3113ed234d8612837f53903a0bd2fea7cee
git_branch=fix/openclaw-real-user-question
```

## Checks

- found_raw_request_log: PASS
- found_api_prompt_log: PASS
- found_compute_prompt_log: PASS
- raw_request_contains_round_1: PASS
- raw_request_contains_round_2: PASS
- api_prompt_contains_round_2_only: PASS
- compute_prompt_matches_api_prompt: PASS
- http_status_is_200: PASS

## Key Evidence

- request_id: 4e8ce165-bf46-47b2-863e-25bdaba9b88a
- api_prompt: DM-S-2-20260418-B 请用一句话总结本轮需求。
- compute_prompt: DM-S-2-20260418-B 请用一句话总结本轮需求。
- status_code: 200

Overall: PASS
