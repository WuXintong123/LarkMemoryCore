"""QLoRA training entry point for the Feishu Office Assistant adapter."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _build_prompt(row: Dict[str, Any]) -> str:
    return (
        "User: 你是飞书办公助手，请严格依据给定材料完成任务。\n"
        f"任务类型：{row['task']}\n"
        f"执行要求：{row['instruction']}\n\n"
        f"材料：\n{row['input']}\n\n"
        "Assistant:"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a QLoRA adapter for the Feishu Office dataset.")
    parser.add_argument("--train-file", type=Path, default=Path("competition/feishu_office/data/train.jsonl"))
    parser.add_argument("--validation-file", type=Path, default=Path("competition/feishu_office/data/validation.jsonl"))
    parser.add_argument("--base-model", default="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-seq-length", type=int, default=1536)
    parser.add_argument("--max-steps", type=int, default=120)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--train-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    args = parser.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    train_rows = _load_rows(args.train_file)
    validation_rows = _load_rows(args.validation_file)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.float16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(
        model,
        LoraConfig(
            task_type="CAUSAL_LM",
            r=64,
            lora_alpha=128,
            lora_dropout=0.05,
            bias="none",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
    )

    def tokenize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        prompt = _build_prompt(row)
        target = row["output"].strip()
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        target_ids = tokenizer(target, add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
        input_ids = (prompt_ids + target_ids)[: args.max_seq_length]
        labels = ([-100] * len(prompt_ids) + target_ids)[: args.max_seq_length]
        attention_mask = [1] * len(input_ids)
        return {
            "input_ids": input_ids,
            "labels": labels,
            "attention_mask": attention_mask,
        }

    train_dataset = Dataset.from_list(train_rows).map(tokenize_row, remove_columns=list(train_rows[0].keys()))
    validation_dataset = Dataset.from_list(validation_rows).map(
        tokenize_row,
        remove_columns=list(validation_rows[0].keys()),
    )

    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        overwrite_output_dir=True,
        per_device_train_batch_size=args.train_batch_size,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        logging_steps=10,
        save_steps=40,
        eval_strategy="no",
        save_strategy="steps",
        max_steps=args.max_steps,
        bf16=False,
        fp16=True,
        warmup_ratio=0.03,
        report_to=[],
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            padding=True,
            label_pad_token_id=-100,
            return_tensors="pt",
        ),
    )
    train_result = trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))

    summary = {
        "base_model": args.base_model,
        "output_dir": str(args.output_dir),
        "max_steps": args.max_steps,
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "final_train_loss": train_result.training_loss,
        "cuda_available": torch.cuda.is_available(),
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "train_batch_size": args.train_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
    }
    (args.output_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
