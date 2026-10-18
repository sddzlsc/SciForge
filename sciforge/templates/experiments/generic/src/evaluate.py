"""评估入口：加载 checkpoint.json 中的参数，输出 eval.json。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a generic experiment checkpoint")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", default="outputs/eval")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from src.data import build_arrays

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    _, _, X_val, y_val = build_arrays(config)
    checkpoint = json.loads(Path(args.checkpoint).read_text(encoding="utf-8"))
    params = checkpoint.get("params", {})
    w = np.asarray(params.get("w", np.zeros((X_val.shape[1], 1))), dtype=np.float32).reshape(-1)
    b = float(np.asarray(params.get("b", [0.0]), dtype=np.float32).reshape(-1)[0])
    pred = X_val @ w + b
    mse = float(np.mean((pred - y_val) ** 2))
    ss_res = float(np.sum((y_val - pred) ** 2))
    ss_tot = float(np.sum((y_val - y_val.mean()) ** 2)) or 1e-12
    result = {
        "checkpoint": str(args.checkpoint),
        "mse": round(mse, 6),
        "rmse": round(float(np.sqrt(mse)), 6),
        "r2": round(1.0 - ss_res / ss_tot, 6),
        "num_samples": int(len(y_val)),
    }
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"eval mse={result['mse']:.6f} r2={result['r2']:.4f} -> {out_dir / 'eval.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
