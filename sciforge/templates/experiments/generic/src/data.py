"""数据加载：合成回归数据 / CSV。

返回 numpy 数组；真实项目可直接替换本函数，保持签名 `build_arrays(cfg)`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def build_arrays(cfg: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    data_cfg = cfg.get("data", {}) or {}
    name = str(data_cfg.get("name", "synthetic-regression")).lower()
    if name in ("synthetic-regression", "synthetic", "toy"):
        rng = np.random.default_rng(int(cfg.get("seed", 42)))
        n_samples = int(data_cfg.get("n_samples", 512))
        n_features = int(data_cfg.get("n_features", 8))
        noise = float(data_cfg.get("noise", 0.1))
        X = rng.normal(size=(n_samples, n_features)).astype(np.float32)
        true_w = rng.normal(size=(n_features, 1)).astype(np.float32)
        y = (X @ true_w).squeeze(-1) + noise * rng.normal(size=n_samples).astype(np.float32)
        split = int(n_samples * 0.8)
        return X[:split], y[:split], X[split:], y[split:]
    if name == "csv":
        path = Path(str(data_cfg.get("csv_path", "./data/train.csv")))
        if not path.exists():
            raise SystemExit(f"CSV 数据不存在：{path}（请修改 configs/default.yaml 的 data.csv_path）")
        raw = np.loadtxt(path, delimiter=",", skiprows=1)
        X, y = raw[:, :-1].astype(np.float32), raw[:, -1].astype(np.float32)
        split = int(len(X) * 0.8)
        return X[:split], y[:split], X[split:], y[split:]
    raise ValueError(f"未知数据集: {name}（支持 synthetic-regression/csv）")
