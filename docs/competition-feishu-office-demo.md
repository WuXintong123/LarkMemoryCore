# Feishu Office Assistant Demo Runbook

## Runtime Baseline

The competition demo reuses the active isolated runtime shape on
`buddy-ascend`:

- API: `127.0.0.1:18100`
- compute: `0.0.0.0:19100`
- tuned daemon: `127.0.0.1:19600`

The legacy `~/ruyi-serving` systemd deployment is not the competition baseline.

## Start / Stop

```bash
cd /home/huangyiheng/src/ruyi-serving-feishu-live-20260416
./ops/feishu_office_competition_preflight.sh
./ops/feishu_office_competition_start.sh
./ops/feishu_office_competition_stop.sh
```

## What The Start Script Does

- resolves `RUYI_API_KEY` from `~/.openclaw/.env`
- writes a competition-specific `models.json`
- starts the tuned-model daemon
- starts `compute_server`
- starts `api_server`
- restarts the OpenClaw gateway against the same `18100` API base URL
- enables the decision memory engine at `.run/feishu-office-competition/memory/decision_memory.sqlite3`

## Demo Checklist

1. `curl http://127.0.0.1:18100/health`
2. `curl -H "Authorization: Bearer $RUYI_API_KEY" http://127.0.0.1:18100/v1/models`
3. Run real integration and small-sample benchmark
4. Run the Feishu/OpenClaw acceptance matrix using:

```bash
cd /home/huangyiheng/src/ruyi-serving-feishu-live-20260416
./ops/openclaw_feishu_buddy_ascend_check.sh \
  --scenario dm-nonstream \
  --trace-token DM-NS-1-20260418-A \
  --trace-token DM-NS-2-20260418-B
```

Set the explicit competition log paths when checking the isolated runtime:

```bash
export RUYI_FEISHU_API_LOG_PATH=.run/feishu-office-competition/logs/api.log
export RUYI_FEISHU_COMPUTE_LOG_PATH=.run/feishu-office-competition/logs/compute.log
```

For the exact end-to-end commands, message texts, report directories, and the
streaming wait rule, use `docs/competition-feishu-office-reproduction.md`.

## OpenClaw

The OpenClaw binary is resolved from the NVM installation on `buddy-ascend`:

- `~/.nvm/versions/node/v22.22.2/bin/openclaw`

The start script uses this path automatically.

## Executed Runtime Results

Service-side results already executed on 2026-04-18:

- `cmake --preset linux-debug`
- `cmake --build --preset linux-debug-build --target generate_python_proto compute_server compute_server_tests`
- `ctest --preset linux-debug-test --output-on-failure --label-regex "cpp|python-unit"`
- `python3 -m pytest -q tests/python/test_feishu_office_dataset_contracts.py`
- `python3 -m pytest -q tests/python/test_production_behaviors.py -k "openclaw or feishu or latest_user or debug_trace"`
- `python3 -m pytest -q tests/python/test_grpc_contracts.py`
- baseline real integration: `2 passed in 224.49s`
- tuned real integration: `2 passed in 26.21s`

## Feishu Acceptance Result

The four required Feishu scenarios were completed on 2026-04-18 and all passed:

1. DM + non-stream
2. DM + stream
3. Group `@bot` + non-stream
4. Group `@bot` + stream

The archived result is recorded in
`docs/openclaw-feishu-verification-note-2026-04-18.md`.
