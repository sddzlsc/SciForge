"""微调入口：HuggingFace Trainer 风格。

- 默认：transformers + Trainer 真实微调。
- ``--smoke``：纯标准库模拟 token 分类训练，CPU 秒级、无需 transformers/数据集。
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fine-tune a text classifier")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args(argv)


def load_config(path: str | Path, args: argparse.Namespace) -> dict[str, Any]:
    path = Path(path)
    config: dict[str, Any] = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}) or {}
    config.setdefault("project", "experiment")
    config.setdefault("seed", 42)
    config.setdefault("train", {})
    if args.epochs is not None:
        config["train"]["epochs"] = args.epochs
    if args.lr is not None:
        config["train"]["lr"] = args.lr
    return config


def _write(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_smoke(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """纯标准库：合成 token 序列 + 多项式朴素贝叶斯，秒级完成。"""
    random.seed(int(config.get("seed", 42)))
    epochs = int(args.epochs or 3)
    vocab_size, seq_len, num_labels, n_train, n_val = 64, 12, 2, 120, 40
    train = [([random.randrange(vocab_size) for _ in range(seq_len)], random.randrange(num_labels)) for _ in range(n_train)]
    val = [([random.randrange(vocab_size) for _ in range(seq_len)], random.randrange(num_labels)) for _ in range(n_val)]
    counts = [[1.0] * vocab_size for _ in range(num_labels)]
    priors = [1.0] * num_labels
    history: list[dict[str, Any]] = []
    for epoch in range(1, epochs + 1):
        loss_sum = 0.0
        for tokens, label in train:
            scores = []
            for c in range(num_labels):
                score = math.log(priors[c] / sum(priors))
                for token in tokens:
                    score += math.log(counts[c][token] / sum(counts[c]))
                scores.append(score)
            top = max(scores)
            probs = [math.exp(s - top) for s in scores]
            total = sum(probs)
            probs = [p / total for p in probs]
            loss_sum -= math.log(max(probs[label], 1e-9))
            for token in tokens:
                counts[label][token] += 1.0
            priors[label] += 1.0
        val_correct = 0
        for tokens, label in val:
            scores = []
            for c in range(num_labels):
                score = math.log(priors[c] / sum(priors))
                for token in tokens:
                    score += math.log(counts[c][token] / sum(counts[c]))
                scores.append(score)
            val_correct += int(max(range(num_labels), key=lambda c: scores[c]) == label)
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(loss_sum / max(1, len(train)), 6),
                "val_acc": round(val_correct / max(1, len(val)), 4),
            }
        )
    best = max(history, key=lambda row: row["val_acc"])
    out_dir = Path(args.output_dir or "outputs/smoke")
    payload = {
        "run_id": args.name or "smoke",
        "project": config.get("project", "experiment"),
        "smoke": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "epochs": history,
        "best": {"epoch": best["epoch"], "val_acc": best["val_acc"]},
        "final": history[-1],
    }
    _write(out_dir, payload)
    print(f"SMOKE OK: {len(history)} epochs, best val_acc={best['val_acc']:.4f} -> {out_dir / 'metrics.json'}")
    return payload


def run_train(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import torch
    from transformers import DataCollatorWithPadding, Trainer, TrainingArguments

    from src.data import build_datasets
    from src.model import build_model, build_tokenizer

    seed = int(config.get("seed", 42))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    tokenizer = build_tokenizer(config)
    train_ds, val_ds, num_labels = build_datasets(config, tokenizer)
    model = build_model(config, num_labels)
    cfg_train = config.get("train", {}) or {}
    out_dir = Path(args.output_dir or (config.get("output", {}) or {}).get("dir", "outputs"))
    out_dir.mkdir(parents=True, exist_ok=True)

    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        return {"accuracy": float((preds == labels).mean())}

    training_args = TrainingArguments(
        output_dir=str(out_dir),
        num_train_epochs=float(cfg_train.get("epochs", 3)),
        per_device_train_batch_size=int(cfg_train.get("batch_size", 16)),
        per_device_eval_batch_size=int(cfg_train.get("eval_batch_size", 64)),
        learning_rate=float(cfg_train.get("lr", 2e-5)),
        weight_decay=float(cfg_train.get("weight_decay", 0.01)),
        warmup_ratio=float(cfg_train.get("warmup_ratio", 0.06)),
        gradient_accumulation_steps=int(cfg_train.get("gradient_accumulation_steps", 1)),
        fp16=bool(cfg_train.get("fp16")) and torch.cuda.is_available(),
        logging_steps=10,
        save_strategy="epoch",
        report_to=[],
        seed=seed,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=compute_metrics,
    )
    trainer.train()
    metrics = trainer.evaluate()
    history = [
        {"epoch": int(entry.get("epoch", 0) + 1), **{f"train_{k}": v for k, v in entry.items() if k in ("loss", "learning_rate")}}
        for entry in trainer.state.log_history
        if "loss" in entry
    ]
    payload = {
        "run_id": args.name or str(config.get("project", "experiment")),
        "project": config.get("project", "experiment"),
        "epochs": history,
        "best": {"eval_accuracy": round(float(metrics.get("eval_accuracy", 0.0)), 4)},
        "final": {f"eval_{key}": round(float(value), 6) for key, value in metrics.items() if isinstance(value, (int, float))},
    }
    _write(out_dir, payload)
    trainer.save_model(str(out_dir / "final_model"))
    print(f"finished: {payload['final']}; metrics -> {out_dir / 'metrics.json'}")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config, args)
    if args.smoke:
        run_smoke(args, config)
    else:
        run_train(args, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
