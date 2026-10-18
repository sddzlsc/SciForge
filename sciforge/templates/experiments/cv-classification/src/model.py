"""模型工厂：torchvision ResNet 或轻量自定义 CNN。"""

from __future__ import annotations

from typing import Any

import torch
from torch import nn


class SmallCNN(nn.Module):
    """3 个卷积块的轻量 CNN，适合 CIFAR-10 快速实验。"""

    def __init__(self, num_classes: int = 10, dropout: float = 0.0) -> None:
        super().__init__()
        def block(cin: int, cout: int) -> nn.Sequential:
            return nn.Sequential(
                nn.Conv2d(cin, cout, 3, padding=1, bias=False),
                nn.BatchNorm2d(cout),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(2),
            )
        self.features = nn.Sequential(block(3, 64), block(64, 128), block(128, 256))
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()
        self.fc = nn.Linear(256, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.pool(self.features(x)).flatten(1)
        return self.fc(self.dropout(x))


def build_model(cfg: dict[str, Any]) -> nn.Module:
    model_cfg = cfg.get("model", {}) or {}
    data_cfg = cfg.get("data", {}) or {}
    name = str(model_cfg.get("name", "resnet18")).lower()
    num_classes = int(data_cfg.get("num_classes", 10))
    dropout = float(model_cfg.get("dropout", 0.0))
    if name == "smallcnn":
        return SmallCNN(num_classes=num_classes, dropout=dropout)
    if name in ("resnet18", "resnet34", "resnet50"):
        from torchvision import models

        weights = "DEFAULT" if bool(model_cfg.get("pretrained")) else None
        model = getattr(models, name)(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, num_classes)
        return model
    raise ValueError(f"未知模型: {name}（支持 resnet18/resnet34/resnet50/smallcnn）")
