# Feishu Office Assistant Dataset Note

## Scope

This dataset targets the enterprise office vertical for the OpenClaw + Feishu
competition track. The task design is fixed to five office-facing capabilities:

- knowledge Q&A
- information summary
- meeting minutes
- weekly report / announcement generation
- standardized enterprise response

## Source Strategy

Only real public materials are used:

1. Existing repository documentation and validation material from `ruyi-serving`
2. Official Feishu public product / help content from `https://www.feishu.cn/content/...`
3. Public office-style notices from the National Bureau of Statistics notice listing

No mock samples, no synthetic source documents, and no model-generated pseudo
labels are used in dataset construction.

## Build Artifacts

The dataset pipeline writes the following tracked artifacts under
`competition/feishu_office/data/`:

- `corpus.jsonl`: normalized source corpus
- `all.jsonl`: full supervised dataset
- `train.jsonl`
- `validation.jsonl`
- `test.jsonl`
- `dataset_manifest.json`
- `quality_report.json`

Each JSONL row uses the fixed schema:

- `id`
- `task`
- `source_title`
- `source_url`
- `license`
- `instruction`
- `input`
- `output`
- `grounding`
- `split`

## Construction Rules

- split assignment is source-level, never chunk-level
- validation and test sources do not overlap with training sources
- each source is chunked deterministically
- all outputs are created by deterministic extraction / restructuring rules
- grounding excerpts always come from the same source chunk used to build the sample

## Quality Gates

The dataset validator enforces:

- required schema fields
- non-empty instruction / input / output / grounding
- supported task names only
- at least 1000 training rows
- at least 200 held-out rows
- no source overlap across `train`, `validation`, and `test`

## Executed Build Result

Executed on 2026-04-18 on `buddy-ascend`:

- document count: `34`
- total supervised rows: `1595`
- train rows: `1070`
- validation rows: `180`
- test rows: `345`
- held-out rows: `525`

The generated `quality_report.json` also records:

- average input chars: `446.71`
- average output chars: `761.64`
- source split counts: `23 train / 5 validation / 6 test`

## Rebuild

```bash
cd /home/huangyiheng/src/ruyi-serving-feishu-live-20260416
python3 -m competition.feishu_office.build_dataset
python3 -m competition.feishu_office.validate_dataset
```

For the full verified sequence, including the exact source paths and expected
counts, use `docs/competition-feishu-office-reproduction.md`.
