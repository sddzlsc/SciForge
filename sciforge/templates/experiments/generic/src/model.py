"""模型：线性回归 / 单隐层 MLP（numpy 手写，便于无 torch 环境跑通）。"""

from __future__ import annotations

from typing import Any

import numpy as np


class LinearModel:
    """最小二乘意义上的线性模型；用 SGD + L2 训练。"""

    def __init__(self, n_features: int, lr: float = 0.05, l2: float = 1e-4, seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        self.w = rng.normal(scale=0.01, size=(n_features, 1)).astype(np.float32)
        self.b = np.zeros(1, dtype=np.float32)
        self.lr = float(lr)
        self.l2 = float(l2)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (X @ self.w + self.b).squeeze(-1)

    def update(self, X: np.ndarray, y: np.ndarray) -> float:
        pred = self.predict(X)
        residual = pred - y
        loss = float(np.mean(residual**2))
        grad_w = (2.0 / len(X)) * (X.T @ residual).reshape(-1, 1) + self.l2 * self.w
        grad_b = np.array([2.0 / len(X) * float(residual.sum())], dtype=np.float32)
        self.w -= self.lr * grad_w.astype(np.float32)
        self.b -= self.lr * grad_b
        return loss


class MLPModel(LinearModel):
    """单隐层 tanh MLP；用反向传播更新。"""

    def __init__(self, n_features: int, hidden_dim: int = 16, lr: float = 0.05, l2: float = 1e-4, seed: int = 42) -> None:
        super().__init__(n_features, lr=lr, l2=l2, seed=seed)
        rng = np.random.default_rng(seed + 1)
        self.W1 = rng.normal(scale=0.1, size=(n_features, hidden_dim)).astype(np.float32)
        self.b1 = np.zeros(hidden_dim, dtype=np.float32)
        self.w2 = rng.normal(scale=0.1, size=(hidden_dim, 1)).astype(np.float32)
        self.b2 = np.zeros(1, dtype=np.float32)
        self.hidden_dim = hidden_dim

    def predict(self, X: np.ndarray) -> np.ndarray:
        hidden = np.tanh(X @ self.W1 + self.b1)
        return (hidden @ self.w2 + self.b2).squeeze(-1)

    def update(self, X: np.ndarray, y: np.ndarray) -> float:
        hidden = np.tanh(X @ self.W1 + self.b1)
        pred = (hidden @ self.w2 + self.b2).squeeze(-1)
        residual = pred - y
        loss = float(np.mean(residual**2))
        d_out = (2.0 / len(X)) * residual[:, None]
        grad_w2 = hidden.T @ d_out + self.l2 * self.w2
        grad_b2 = np.array([float(d_out.sum())], dtype=np.float32)
        d_hidden = (d_out @ self.w2.T) * (1.0 - hidden**2)
        grad_W1 = X.T @ d_hidden + self.l2 * self.W1
        grad_b1 = d_hidden.sum(axis=0)
        self.W1 -= self.lr * grad_W1.astype(np.float32)
        self.b1 -= self.lr * grad_b1.astype(np.float32)
        self.w2 -= self.lr * grad_w2.astype(np.float32)
        self.b2 -= self.lr * grad_b2
        return loss


def build_model(cfg: dict[str, Any], n_features: int):
    model_cfg = cfg.get("model", {}) or {}
    train_cfg = cfg.get("train", {}) or {}
    name = str(model_cfg.get("name", "linear")).lower()
    lr = float(train_cfg.get("lr", 0.05))
    l2 = float(train_cfg.get("l2", 1e-4))
    seed = int(cfg.get("seed", 42))
    if name == "mlp":
        return MLPModel(n_features, hidden_dim=int(model_cfg.get("hidden_dim", 16)), lr=lr, l2=l2, seed=seed)
    return LinearModel(n_features, lr=lr, l2=l2, seed=seed)
