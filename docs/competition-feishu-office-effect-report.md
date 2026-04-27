# Feishu Office Assistant Effect Comparison Report

## Compared Models

- baseline: `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B`
- tuned: `ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice`

## Evaluation Path

The comparison script is:

```bash
cd /home/huangyiheng/src/ruyi-serving-feishu-live-20260416
python3 competition/feishu_office/evaluate_models.py \
  --base-url http://127.0.0.1:18100 \
  --api-key "$RUYI_API_KEY" \
  --baseline-model deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B \
  --tuned-model ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice \
  --test-file competition/feishu_office/data/test.jsonl \
  --sample-count 3 \
  --max-tokens 96 \
  --request-timeout-s 180 \
  --output-json competition/feishu_office/artifacts/eval/evaluation.json \
  --output-md competition/feishu_office/artifacts/eval/evaluation.md
```

Run the baseline and tuned model evaluations sequentially through the same
script invocation above. Do not launch any other real integration or benchmark
command in parallel, because the competition runtime is configured with a
single compute slot.

## Metrics

The current comparison uses three real-output metrics:

- average latency in milliseconds
- format compliance against task-specific required section headers
- character-level F1 against the held-out deterministic target

## Executed Results

### Small-Sample Evaluation

Executed artifact:

- `competition/feishu_office/artifacts/eval/evaluation.json`
- `competition/feishu_office/artifacts/eval/evaluation.md`

Run settings:

- date: 2026-04-18
- sample count: `3`
- held-out source selection: shortest held-out rows from `test.jsonl`
- max tokens: `96`

Observed metrics:

| Model | Success / Failure | Avg latency (ms) | Avg format compliance | Avg char F1 |
| --- | ---: | ---: | ---: | ---: |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B` | `3 / 0` | `151614.08` | `0.0` | `0.1802` |
| `ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice` | `3 / 0` | `6313.85` | `0.0` | `0.0787` |

Interpretation:

- These three held-out rows were short repository-fragment tasks rather than
  representative office prompts.
- On this narrow slice, the tuned adapter underperformed the baseline on
  character-level overlap.
- The tuned model still reduced latency substantially and was easier to steer
  into structured office output on targeted prompts.

### Real Benchmark

Executed with `BENCHMARK_MAX_SAMPLES=1` against the same isolated runtime.

| Model | Latency avg (ms) | QPS | Generated tokens / second |
| --- | ---: | ---: | ---: |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B` | `75559.45` | `0.013` | `0.040` |
| `ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice` | `4114.18` | `0.243` | `15.556` |

### Qualitative Office-Prompt Comparison

Prompt:

`请根据以下材料输出正式周报格式，包含本周进展、风险与关注、下周计划。材料：本周完成飞书办公助手真实数据集构建与验证，训练环境已在 RTX 3090 上部署完成，下一步将接入调优模型与 OpenClaw 联调。`

Observed behavior:

- Baseline model returned reflective free-form reasoning and did not follow the
  requested section structure.
- Tuned model returned section headers `风险与关注` and `下周计划`, showing
  improved controllability for office-format prompts.
- On this office prompt, the tuned model finished in about `13.7 s`; the
  baseline took about `75.7 s`.

## Reporting Rule

Only actually executed evaluation runs may be summarized here. If the tuned
model or runtime was not evaluated, note that explicitly instead of implying a
pass.
