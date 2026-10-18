"""评估入口：加载 LoRA adapter，计算验证集 perplexity / loss。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import yaml


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate an SFT adapter")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--adapter", required=True, help="adapter 目录")
    parser.add_argument("--output-dir", default="outputs/eval")
    parser.add_argument("--max-batches", type=int, default=50)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from src.data import build_datasets

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    base_name = str((config.get("model", {}) or {}).get("name"))
    tokenizer = AutoTokenizer.from_pretrained(args.adapter)
    base = AutoModelForCausalLM.from_pretrained(base_name, torch_dtype=torch.float32)
    model = PeftModel.from_pretrained(base, args.adapter).eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    _, val_ds = build_datasets(config, tokenizer)
    total_loss, total_tokens = 0.0, 0
    for index, sample in enumerate(val_ds):
        if index >= args.max_batches:
            break
        input_ids = torch.tensor([sample["input_ids"]], device=device)
        labels = torch.tensor([sample["labels"]], device=device)
        with torch.no_grad():
            loss = model(input_ids=input_ids, labels=labels).loss
        count = int((labels != -100).sum())
        total_loss += float(loss) * max(1, count)
        total_tokens += max(1, count)
    eval_loss = total_loss / max(1, total_tokens)
    result = {
        "adapter": args.adapter,
        "eval_loss": round(eval_loss, 6),
        "perplexity": round(math.exp(min(eval_loss, 50)), 4),
        "num_samples": int(min(len(val_ds), args.max_batches)),
    }
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"eval loss={result['eval_loss']:.4f} ppl={result['perplexity']:.2f} -> {out_dir / 'eval.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
