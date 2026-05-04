# Feishu Office Evaluation

- Baseline model: `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B`
- Tuned model: `ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice`
- Sample count: 3
- Baseline success/failure: 3/0
- Tuned success/failure: 3/0

| Model | Avg latency (ms) | Avg format compliance | Avg char F1 |
| --- | ---: | ---: | ---: |
| deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B | 151614.08 | 0.0 | 0.1802 |
| ruyi-office/DeepSeek-R1-Distill-Qwen-1.5B-FeishuOffice | 6313.85 | 0.0 | 0.0787 |