"""LoRA fine-tuning of a small base model on banking77 intent classification.

    python train_lora.py --base <hf model id> --out runs/lora [--max-steps N] [--train-rows N]
"""
import argparse
import json
import os

from datasets import load_dataset
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding,
                          Trainer, TrainingArguments)


def load_banking77(train_rows):
    ds = load_dataset("mteb/banking77")
    return ds["train"].shuffle(seed=0).select(range(train_rows)), ds["test"].select(range(train_rows // 4))


def tokenize(split, tokenizer):
    return split.map(lambda b: tokenizer(b["text"], truncation=True, max_length=64), batched=True)


def build_model(base, num_labels, rank):
    model = AutoModelForSequenceClassification.from_pretrained(base, num_labels=num_labels)
    config = LoraConfig(task_type=TaskType.SEQ_CLS, r=rank, lora_alpha=2 * rank, lora_dropout=0.1,
                        target_modules=["query", "value"])
    return get_peft_model(model, config)


def train(model, train_split, tokenizer, out, max_steps):
    args = TrainingArguments(output_dir=out, max_steps=max_steps, per_device_train_batch_size=16,
                             learning_rate=5e-4, logging_steps=1, save_strategy="no", report_to=[])
    trainer = Trainer(model=model, args=args, train_dataset=train_split,
                      data_collator=DataCollatorWithPadding(tokenizer))
    trainer.train()
    return trainer


def evaluate(trainer, eval_split):
    return trainer.evaluate(eval_split)


def save_adapter(model, out):
    model.save_pretrained(os.path.join(out, "adapter"))


def write_metrics(metrics, out):
    with open(os.path.join(out, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="boltuix/bert-micro")
    ap.add_argument("--out", default="runs/lora")
    ap.add_argument("--max-steps", type=int, default=2)
    ap.add_argument("--train-rows", type=int, default=64)
    ap.add_argument("--rank", type=int, default=8)
    a = ap.parse_args()

    train_split, eval_split = load_banking77(a.train_rows)
    tokenizer = AutoTokenizer.from_pretrained(a.base)
    train_split, eval_split = tokenize(train_split, tokenizer), tokenize(eval_split, tokenizer)
    model = build_model(a.base, 77, a.rank)
    trainer = train(model, train_split, tokenizer, a.out, a.max_steps)
    metrics = evaluate(trainer, eval_split)
    save_adapter(model, a.out)
    write_metrics(metrics, a.out)


if __name__ == "__main__":
    main()
