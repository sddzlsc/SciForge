"""训练入口。

两种模式：

- ``--smoke``：纯标准库 + 随机合成数据，CPU 上秒级跑完，验证实验链路（无需 torch/数据集）。
- 默认：真实 PyTorch 训练，读取 configs/*.yaml，每个 epoch 追加写入 metrics.json。

metrics.json 格式（`sciforge exp results` 直接消费）::

    {"run_id": "...", "epochs": [{"epoch": 1, "train_loss": .., "val_loss": .., "val_acc": ..}],
     "best": {"epoch": .., "val_acc": ..}, "final": {...}}
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a CV classification model")
    parser.add_argument("--config", default="configs/default.yaml", help="YAML 配置文件")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认取 config.output.dir）")
    parser.add_argument("--name", default=None, help="实验名（默认取 config.project）")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--device", default=None, help="auto|cpu|cuda|mps")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--smoke", action="store_true", help="CPU 离线自检（不依赖 torch）")
    return parser.parse_args(argv)


def load_config(path: str | Path, args: argparse.Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {}
    path = Path(path)
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise SystemExit(f"配置文件格式错误（应为 mapping）：{path}")
        config = loaded
    config.setdefault("project", "experiment")
    config.setdefault("seed", 42)
    config.setdefault("train", {})
    if args.epochs is not None:
        config["train"]["epochs"] = args.epochs
    if args.batch_size is not None:
        config["train"]["batch_size"] = args.batch_size
    if args.lr is not None:
        config["train"]["lr"] = args.lr
    if args.seed is not None:
        config["seed"] = args.seed
    if args.device:
        config["device"] = args.device
    return config


# ---------------------------------------------------------------------------
# 离线 smoke：不依赖 torch / 数据集，1 分钟内跑通
# ---------------------------------------------------------------------------


def run_smoke(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    seed = int(config.get("seed", 42))
    random.seed(seed)
    epochs = int(args.epochs or 3)
    num_samples, num_features, num_classes = 240, 16, 5
    # 合成随机线性可分数据：y = argmax(W x + b)
    weights = [[random.uniform(-1, 1) for _ in range(num_features)] for _ in range(num_classes)]
    samples = [
        (
            [random.gauss(0, 1) for _ in range(num_features)],
            max(range(num_classes), key=lambda c: sum(w * x for w, x in zip(weights[c], xs))),
        )
        for xs in ([random.gauss(0, 1) for _ in range(num_features)] for _ in range(num_samples))
    ]
    split = int(len(samples) * 0.8)
    train_set, val_set = samples[:split], samples[split:]
    theta = [0.0] * (num_features * num_classes + num_classes)

    def logits(xs: list[float]) -> list[float]:
        out = []
        for c in range(num_classes):
            base = num_features * c
            out.append(sum(theta[base + i] * xs[i] for i in range(num_features)) + theta[-num_classes + c])
        return out

    def softmax(values: list[float]) -> list[float]:
        top = max(values)
        exps = [math.exp(v - top) for v in values]
        total = sum(exps)
        return [v / total for v in exps]

    lr = 0.05
    history: list[dict[str, Any]] = []
    for epoch in range(1, epochs + 1):
        random.shuffle(train_set)
        loss_sum = 0.0
        correct = 0
        for xs, label in train_set:
            probs = softmax(logits(xs))
            target = [1.0 if c == label else 0.0 for c in range(num_classes)]
            grads = [probs[c] - target[c] for c in range(num_classes)]
            loss_sum -= math.log(max(probs[label], 1e-9))
            correct += int(max(range(num_classes), key=lambda c: probs[c]) == label)
            for c in range(num_classes):
                base = num_features * c
                for i in range(num_features):
                    theta[base + i] -= lr * grads[c] * xs[i]
                theta[-num_classes + c] -= lr * grads[c]
        train_loss = loss_sum / max(1, len(train_set))
        train_acc = correct / max(1, len(train_set))
        val_correct = 0
        val_loss = 0.0
        for xs, label in val_set:
            probs = softmax(logits(xs))
            val_loss -= math.log(max(probs[label], 1e-9))
            val_correct += int(max(range(num_classes), key=lambda c: probs[c]) == label)
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(train_loss, 6),
                "train_acc": round(train_acc, 4),
                "val_loss": round(val_loss / max(1, len(val_set)), 6),
                "val_acc": round(val_correct / max(1, len(val_set)), 4),
            }
        )
    best = max(history, key=lambda row: row["val_acc"])
    out_dir = Path(args.output_dir or "outputs/smoke")
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "run_id": args.name or "smoke",
        "project": config.get("project", "experiment"),
        "smoke": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "epochs": history,
        "best": {"epoch": best["epoch"], "val_acc": best["val_acc"]},
        "final": {key: history[-1][key] for key in ("epoch", "train_loss", "val_loss", "val_acc")},
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SMOKE OK: {len(history)} epochs, best val_acc={best['val_acc']:.4f} -> {out_dir / 'metrics.json'}")
    return metrics


# ---------------------------------------------------------------------------
# 真实训练
# ---------------------------------------------------------------------------


def resolve_device(requested: str | None):
    import torch

    wanted = (requested or "auto").lower()
    if wanted == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if wanted == "cuda" and not torch.cuda.is_available():
        raise SystemExit("请求了 CUDA 但 torch.cuda.is_available()==False")
    return torch.device(wanted)


def run_train(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    import torch

    from src.data import build_dataloaders
    from src.model import build_model

    seed = int(config.get("seed", 42))
    random.seed(seed)
    torch.manual_seed(seed)
    device = resolve_device(config.get("device"))
    train_loader, val_loader, num_classes = build_dataloaders(config)
    config.setdefault("data", {})["num_classes"] = num_classes
    model = build_model(config).to(device)
    train_cfg = config.get("train", {}) or {}
    epochs = int(train_cfg.get("epochs", 100))
    lr = float(train_cfg.get("lr", 1e-3))
    weight_decay = float(train_cfg.get("weight_decay", 0.0))
    optimizer_name = str(train_cfg.get("optimizer", "adamw")).lower()
    params = model.parameters()
    if optimizer_name == "sgd":
        optimizer = torch.optim.SGD(params, lr=lr, momentum=0.9, weight_decay=weight_decay)
    else:
        optimizer = torch.optim.AdamW(params, lr=lr, weight_decay=weight_decay)
    scheduler_name = str(train_cfg.get("scheduler", "cosine")).lower()
    scheduler = None
    if scheduler_name == "cosine":
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, epochs))
    elif scheduler_name == "step":
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=max(1, epochs // 3), gamma=0.1)
    use_amp = bool(train_cfg.get("amp")) and device.type == "cuda"
    scaler = torch.cuda.amp.GradScaler(enabled=use_amp)
    criterion = torch.nn.CrossEntropyLoss()

    out_dir = Path(args.output_dir or (config.get("output", {}) or {}).get("dir", "outputs"))
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.name or str(config.get("project", "experiment"))
    metrics_path = out_dir / "metrics.json"
    history: list[dict[str, Any]] = []
    best_acc = -1.0
    started = time.time()

    def evaluate() -> tuple[float, float]:
        model.eval()
        loss_sum, correct, total = 0.0, 0, 0
        with torch.no_grad():
            for images, labels in val_loader:
                images, labels = images.to(device), labels.to(device)
                logits = model(images)
                loss_sum += float(criterion(logits, labels)) * labels.size(0)
                correct += int((logits.argmax(dim=1) == labels).sum())
                total += int(labels.size(0))
        return loss_sum / max(1, total), correct / max(1, total)

    for epoch in range(1, epochs + 1):
        model.train()
        loss_sum, correct, total = 0.0, 0, 0
        for images, labels in train_loader:
            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            with torch.cuda.amp.autocast(enabled=use_amp):
                logits = model(images)
                loss = criterion(logits, labels)
            if use_amp:
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                optimizer.step()
            loss_sum += float(loss) * labels.size(0)
            correct += int((logits.argmax(dim=1) == labels).sum())
            total += int(labels.size(0))
        if scheduler is not None:
            scheduler.step()
        val_loss, val_acc = evaluate()
        row = {
            "epoch": epoch,
            "train_loss": round(loss_sum / max(1, total), 6),
            "train_acc": round(correct / max(1, total), 4),
            "val_loss": round(val_loss, 6),
            "val_acc": round(val_acc, 4),
            "lr": round(float(optimizer.param_groups[0]["lr"]), 8),
            "elapsed_s": round(time.time() - started, 1),
        }
        history.append(row)
        metrics_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "project": config.get("project", "experiment"),
                    "epochs": history,
                    "best": {"epoch": max(history, key=lambda r: r["val_acc"])["epoch"], "val_acc": max(r["val_acc"] for r in history)},
                    "final": {key: row[key] for key in ("epoch", "train_loss", "val_loss", "val_acc")},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"epoch {epoch}/{epochs} train_loss={row['train_loss']:.4f} val_loss={row['val_loss']:.4f} val_acc={row['val_acc']:.4f}")
        if val_acc > best_acc:
            best_acc = val_acc
            torch.save({"model": model.state_dict(), "config": config, "epoch": epoch}, out_dir / "checkpoint.pt")
    print(f"training finished: best val_acc={best_acc:.4f}; metrics -> {metrics_path}")
    return {"history": history, "best_val_acc": best_acc, "output_dir": str(out_dir)}


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
