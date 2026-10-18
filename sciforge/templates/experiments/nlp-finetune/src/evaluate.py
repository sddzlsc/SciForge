"""评估入口：加载微调后的分类模型，输出 eval.json。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a fine-tuned text classifier")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--model-dir", required=True, help="Trainer 保存的模型目录")
    parser.add_argument("--output-dir", default="outputs/eval")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from src.data import load_examples

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(args.model_dir)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()
    _, val_rows, _ = load_examples(config)
    correct = 0
    with torch.no_grad():
        for row in val_rows:
            inputs = tokenizer(row["text"], truncation=True, max_length=128, return_tensors="pt").to(device)
            logits = model(**inputs).logits
            correct += int(int(logits.argmax(dim=-1).item()) == int(row["label"]))
    result = {"model_dir": args.model_dir, "accuracy": round(correct / max(1, len(val_rows)), 6), "num_samples": len(val_rows)}
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"eval accuracy={result['accuracy']:.4f} -> {out_dir / 'eval.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
