"""LLM SFT 入口：transformers Trainer + peft LoRA。

- 默认：真实指令微调（需要 GPU / transformers / peft）。
- ``--smoke``：纯标准库模拟 next-token 训练，CPU 秒级、不下载任何模型。
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
    parser = argparse.ArgumentParser(description="SFT an LLM with LoRA")
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
    config.setdefault("project", "sft")
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
    """纯标准库：合成 token 序列 + 一阶马尔可夫转移计数，模拟 next-token loss 下降。"""
    random.seed(int(config.get("seed", 42)))
    epochs = int(args.epochs or 3)
    vocab, seq_len, n_samples = 48, 16, 200
    data = [[random.randrange(vocab) for _ in range(seq_len)] for _ in range(n_samples)]
    counts = [[1.0] * vocab for _ in range(vocab)]
    history: list[dict[str, Any]] = []
    for epoch in range(1, epochs + 1):
        loss_sum, tokens = 0.0, 0
        for seq in data:
            for prev, nxt in zip(seq[:-1], seq[1:], strict=False):
                probs = [count / sum(counts[prev]) for count in counts[prev]]
                loss_sum -= math.log(max(probs[nxt], 1e-9))
                counts[prev][nxt] += 1.0
                tokens += 1
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(loss_sum / max(1, tokens), 6),
                "perplexity": round(math.exp(loss_sum / max(1, tokens)), 4),
            }
        )
    best = min(history, key=lambda row: row["train_loss"])
    out_dir = Path(args.output_dir or "outputs/smoke")
    payload = {
        "run_id": args.name or "smoke",
        "project": config.get("project", "sft"),
        "smoke": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "epochs": history,
        "best": {"epoch": best["epoch"], "train_loss": best["train_loss"]},
        "final": history[-1],
    }
    _write(out_dir, payload)
    print(f"SMOKE OK: {len(history)} epochs, best loss={best['train_loss']:.4f} -> {out_dir / 'metrics.json'}")
    return payload


def run_train(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import torch
    from transformers import DataCollatorForLanguageModeling, Trainer, TrainingArguments

    from src.data import build_datasets
    from src.model import build_model, build_tokenizer

    seed = int(config.get("seed", 42))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    tokenizer = build_tokenizer(config)
    train_ds, val_ds = build_datasets(config, tokenizer)
    model = build_model(config)
    cfg_train = config.get("train", {}) or {}
    out_dir = Path(args.output_dir or (config.get("output", {}) or {}).get("dir", "outputs"))
    out_dir.mkdir(parents=True, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=str(out_dir),
        num_train_epochs=float(cfg_train.get("epochs", 3)),
        per_device_train_batch_size=int(cfg_train.get("batch_size", 1)),
        per_device_eval_batch_size=int(cfg_train.get("batch_size", 1)),
        gradient_accumulation_steps=int(cfg_train.get("gradient_accumulation_steps", 8)),
        learning_rate=float(cfg_train.get("lr", 2e-4)),
        weight_decay=float(cfg_train.get("weight_decay", 0.0)),
        warmup_ratio=float(cfg_train.get("warmup_ratio", 0.03)),
        logging_steps=int(cfg_train.get("logging_steps", 10)),
        save_steps=int(cfg_train.get("save_steps", 200)),
        bf16=torch.cuda.is_available(),
        report_to=[],
        seed=seed,
        remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False),
    )
    trainer.train()
    metrics = trainer.evaluate()
    history = [
        {"epoch": int(entry.get("epoch", 0) + 1), **{k: v for k, v in entry.items() if k in ("loss", "learning_rate")}}
        for entry in trainer.state.log_history
        if "loss" in entry
    ]
    payload = {
        "run_id": args.name or str(config.get("project", "sft")),
        "project": config.get("project", "sft"),
        "epochs": history,
        "best": {"eval_loss": round(float(metrics.get("eval_loss", 0.0)), 6)},
        "final": {f"eval_{key}": round(float(value), 6) for key, value in metrics.items() if isinstance(value, (int, float))},
    }
    _write(out_dir, payload)
    trainer.save_model(str(out_dir / "adapter"))
    tokenizer.save_pretrained(str(out_dir / "adapter"))
    print(f"finished: {payload['final']}; adapter -> {out_dir / 'adapter'}")
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
