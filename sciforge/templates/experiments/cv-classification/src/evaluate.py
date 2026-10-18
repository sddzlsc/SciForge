"""评估入口：加载 checkpoint，在验证集上输出 eval.json。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a trained CV checkpoint")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--checkpoint", required=True, help="checkpoint.pt 路径")
    parser.add_argument("--output-dir", default="outputs/eval")
    parser.add_argument("--split", default="val", choices=["val", "test"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    import torch

    from src.data import build_dataloaders
    from src.model import build_model

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    _, val_loader, num_classes = build_dataloaders(config)
    config.setdefault("data", {})["num_classes"] = num_classes
    model = build_model(config)
    checkpoint = torch.load(args.checkpoint, map_location="cpu")
    model.load_state_dict(checkpoint["model"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()
    criterion = torch.nn.CrossEntropyLoss()
    loss_sum, correct, total = 0.0, 0, 0
    with torch.no_grad():
        for images, labels in val_loader:
            images, labels = images.to(device), labels.to(device)
            logits = model(images)
            loss_sum += float(criterion(logits, labels)) * labels.size(0)
            correct += int((logits.argmax(dim=1) == labels).sum())
            total += int(labels.size(0))
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "checkpoint": str(args.checkpoint),
        "split": args.split,
        "loss": round(loss_sum / max(1, total), 6),
        "accuracy": round(correct / max(1, total), 6),
        "num_samples": total,
    }
    (out_dir / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"eval loss={result['loss']:.4f} acc={result['accuracy']:.4f} -> {out_dir / 'eval.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
