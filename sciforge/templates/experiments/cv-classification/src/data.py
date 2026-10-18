"""数据加载：CIFAR 风格图像分类。

真实训练走 torchvision；`--smoke` 自检在 train.py 内用合成数据完成，不会 import 本文件，
因此 CPU 无网络环境下也能快速验证实验链路。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
from torch.utils.data import DataLoader, Dataset, random_split


class SyntheticImageDataset(Dataset):
    """随机张量数据集：完全离线，用于调通训练/评估代码。"""

    def __init__(self, num_samples: int = 1024, num_classes: int = 10, image_size: int = 32) -> None:
        self.images = torch.randn(num_samples, 3, image_size, image_size)
        self.labels = torch.randint(0, num_classes, (num_samples,))

    def __len__(self) -> int:
        return int(self.images.shape[0])

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.images[index], self.labels[index]


def _transforms(image_size: int, train: bool):
    from torchvision import transforms

    ops: list[Any] = []
    if train:
        ops += [transforms.RandomCrop(image_size, padding=4), transforms.RandomHorizontalFlip()]
    ops += [transforms.ToTensor(), transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))]
    return transforms.Compose(ops)


def build_datasets(cfg: dict[str, Any]) -> tuple[Dataset, Dataset, int]:
    """返回 (train_dataset, val_dataset, num_classes)。"""
    data_cfg = cfg.get("data", {}) or {}
    name = str(data_cfg.get("name", "cifar10")).lower()
    image_size = int(data_cfg.get("image_size", 32))
    num_classes = int(data_cfg.get("num_classes", 10))
    if bool(data_cfg.get("synthetic")):
        dataset = SyntheticImageDataset(num_classes=num_classes, image_size=image_size)
        val_size = max(1, int(0.2 * len(dataset)))
        train_set, val_set = random_split(
            dataset, [len(dataset) - val_size, val_size], generator=torch.Generator().manual_seed(42)
        )
        return train_set, val_set, num_classes

    from torchvision import datasets

    root = Path(str(data_cfg.get("root", "./data"))).expanduser()
    if name in ("cifar10", "cifar100"):
        factory = datasets.CIFAR10 if name == "cifar10" else datasets.CIFAR100
        train_set = factory(root=str(root), train=True, download=True, transform=_transforms(image_size, True))
        val_set = factory(root=str(root), train=False, download=True, transform=_transforms(image_size, False))
        return train_set, val_set, len(train_set.classes)
    if name in ("imagefolder", "custom"):
        train_set = datasets.ImageFolder(root / "train", transform=_transforms(image_size, True))
        val_set = datasets.ImageFolder(root / "val", transform=_transforms(image_size, False))
        return train_set, val_set, len(train_set.classes)
    raise ValueError(f"未知数据集: {name}（支持 cifar10/cifar100/imagefolder）")


def build_dataloaders(cfg: dict[str, Any]) -> tuple[DataLoader, DataLoader, int]:
    train_set, val_set, num_classes = build_datasets(cfg)
    data_cfg = cfg.get("data", {}) or {}
    train_cfg = cfg.get("train", {}) or {}
    batch_size = int(train_cfg.get("batch_size", 128))
    workers = int(data_cfg.get("num_workers", 4))
    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=workers, pin_memory=True)
    val_loader = DataLoader(val_set, batch_size=max(1, batch_size * 2), shuffle=False, num_workers=workers)
    return train_loader, val_loader, num_classes
