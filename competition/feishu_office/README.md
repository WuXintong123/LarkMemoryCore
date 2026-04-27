# Feishu Office Assistant Competition Assets

This directory contains the competition-specific assets used to turn the
existing OpenClaw + Feishu + Ruyi Serving stack into a vertical office assistant
delivery:

- `source_manifest.json`: public real-data source manifest
- `dataset_pipeline.py`: source crawling, normalization, splitting, and dataset build logic
- `data/`: generated corpus, train/validation/test sets, manifest, and quality report
- `train_qlora.py`: single-GPU QLoRA training entrypoint
- `evaluate_models.py`: baseline vs tuned evaluation entrypoint
- `runtime/`: persistent tuned-model daemon and compute-compatible CLI shim
- `artifacts/`: training outputs and adapter checkpoints

The competition workflow is:

1. Build the real dataset: `python3 -m competition.feishu_office.build_dataset`
2. Validate the dataset: `python3 -m competition.feishu_office.validate_dataset`
3. Prepare the GPU training environment: `./ops/feishu_office_train_env.sh`
4. Train the adapter with the venv Python
5. Start the competition runtime: `./ops/feishu_office_competition_start.sh`
6. Evaluate baseline and tuned models on held-out samples
7. Run the OpenClaw + Feishu acceptance checklist with the competition runtime logs

For the exact server-side command sequence that was actually verified, use
`docs/competition-feishu-office-reproduction.md` from the repository root.

The dataset build and evaluation scripts use only real public materials and
existing repository artifacts. They do not use mock data or model-generated
pseudo labels.
