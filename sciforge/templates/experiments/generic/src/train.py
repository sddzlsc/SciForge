"""训练入口：numpy 训练循环 + metrics.json 落盘。

- 默认：合成回归/CSV 数据真实训练（仅依赖 numpy/pyyaml）。
- ``--smoke``：纯标准库随机数据，验证链路（无需 numpy）。
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
    parser = argparse.ArgumentParser(description="Generic experiment runner")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args(argv)


def load_config(path: str | Path, args: argparse.Namespace) -> dict[str, Any]:
    path = Path(path)
    config: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    config = config or {}
    config.setdefault("project", "experiment")
    config.setdefault("seed", 42)
    config.setdefault("train", {})
    config.setdefault("data", {})
    if args.epochs is not None:
        config["train"]["epochs"] = args.epochs
    if args.seed is not None:
        config["seed"] = args.seed
    return config


def _write_metrics(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_smoke(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """纯标准库自检：随机数据 + 线性模型，1 秒内完成。"""
    random.seed(int(config.get("seed", 42)))
    epochs = int(args.epochs or 5)
    n_features, n_samples = 8, 128
    data = [
        ([random.gauss(0, 1) for _ in range(n_features)], random.gauss(0, 1))
        for _ in range(n_samples)
    ]
    split = int(n_samples * 0.8)
    train_set, val_set = data[:split], data[split:]
    theta = [0.0] * (n_features + 1)
    lr = 0.05
    history: list[dict[str, Any]] = []

    def predict(xs: list[float]) -> float:
        return sum(theta[i] * xs[i] for i in range(n_features)) + theta[-1]

    for epoch in range(1, epochs + 1):
        random.shuffle(train_set)
        for xs, y in train_set:
            err = predict(xs) - y
            for i in range(n_features):
                theta[i] -= lr * err * xs[i]
            theta[-1] -= lr * err
        train_loss = sum((predict(xs) - y) ** 2 for xs, y in train_set) / len(train_set)
        val_loss = sum((predict(xs) - y) ** 2 for xs, y in val_set) / len(val_set)
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(train_loss, 6),
                "val_loss": round(val_loss, 6),
                "val_rmse": round(math.sqrt(val_loss), 6),
            }
        )
    best = min(history, key=lambda row: row["val_loss"])
    out_dir = Path(args.output_dir or "outputs/smoke")
    payload = {
        "run_id": args.name or "smoke",
        "project": config.get("project", "experiment"),
        "smoke": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "epochs": history,
        "best": {"epoch": best["epoch"], "val_loss": best["val_loss"]},
        "final": history[-1],
    }
    _write_metrics(out_dir, payload)
    print(f"SMOKE OK: {len(history)} epochs, best val_loss={best['val_loss']:.6f} -> {out_dir / 'metrics.json'}")
    return payload


def run_train(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    from src.data import build_arrays
    from src.model import build_model

    seed = int(config.get("seed", 42))
    rng = np.random.default_rng(seed)
    np.random.seed(seed)
    X_train, y_train, X_val, y_val = build_arrays(config)
    model = build_model(config, X_train.shape[1])
    train_cfg = config.get("train", {}) or {}
    epochs = int(train_cfg.get("epochs", 50))
    batch_size = int(train_cfg.get("batch_size", 64))
    out_dir = Path(args.output_dir or (config.get("output", {}) or {}).get("dir", "outputs"))
    history: list[dict[str, Any]] = []
    best_loss = float("inf")
    for epoch in range(1, epochs + 1):
        order = rng.permutation(len(X_train))
        for start in range(0, len(X_train), batch_size):
            batch = order[start : start + batch_size]
            model.update(X_train[batch], y_train[batch])
        train_loss = float(np.mean((model.predict(X_train) - y_train) ** 2))
        val_pred = model.predict(X_val)
        val_loss = float(np.mean((val_pred - y_val) ** 2))
        val_rmse = math.sqrt(val_loss)
        ss_res = float(np.sum((y_val - val_pred) ** 2))
        ss_tot = float(np.sum((y_val - y_val.mean()) ** 2)) or 1e-12
        row = {
            "epoch": epoch,
            "train_loss": round(train_loss, 6),
            "val_loss": round(val_loss, 6),
            "val_rmse": round(val_rmse, 6),
            "val_r2": round(1.0 - ss_res / ss_tot, 6),
        }
        history.append(row)
        payload = {
            "run_id": args.name or str(config.get("project", "experiment")),
            "project": config.get("project", "experiment"),
            "epochs": history,
            "best": {"epoch": min(history, key=lambda r: r["val_loss"])["epoch"], "val_loss": min(r["val_loss"] for r in history)},
            "final": row,
        }
        _write_metrics(out_dir, payload)
        print(f"epoch {epoch}/{epochs} train_loss={train_loss:.6f} val_loss={val_loss:.6f} val_rmse={val_rmse:.6f}")
        if val_loss < best_loss:
            best_loss = val_loss
            (out_dir / "checkpoint.json").write_text(
                json.dumps({"epoch": epoch, "params": model.__dict__, "config": config}, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
    print(f"training finished: best val_loss={best_loss:.6f}; metrics -> {out_dir / 'metrics.json'}")
    return {"history": history, "best_val_loss": best_loss, "output_dir": str(out_dir)}


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
