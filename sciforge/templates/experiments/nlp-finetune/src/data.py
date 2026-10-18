"""数据加载：HF datasets 或本地 JSONL。真实微调路径使用（smoke 不 import 本文件）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_examples(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """返回 (train_examples, val_examples, num_labels)。example: {"text": str, "label": int}"""
    data_cfg = cfg.get("data", {}) or {}
    name = str(data_cfg.get("name", "glue-sst2")).lower()
    text_field = str(data_cfg.get("text_field", "text"))
    label_field = str(data_cfg.get("label_field", "label"))
    num_labels = int(data_cfg.get("num_labels", 2))
    if name == "local-jsonl":
        def read(path: str) -> list[dict[str, Any]]:
            file = Path(path).expanduser()
            if not file.exists():
                raise SystemExit(
                    f"找不到数据文件 {file}；请准备 JSONL（每行 {{\"text\": ..., \"label\": ...}}）"
                    "或把 data.name 改成 glue-sst2。"
                )
            rows = []
            for line in file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                rows.append({"text": str(record.get(text_field, "")), "label": int(record.get(label_field, 0))})
            return rows

        return (
            read(str(data_cfg.get("train_file", "./data/train.jsonl"))),
            read(str(data_cfg.get("val_file", "./data/val.jsonl"))),
            num_labels,
        )
    from datasets import load_dataset

    if name in ("glue-sst2", "sst2"):
        dataset = load_dataset("glue", "sst2")
        train, val = dataset["train"], dataset["validation"]
        num_labels = 2
    elif name in ("ag-news", "ag_news"):
        dataset = load_dataset("ag_news")
        train, val = dataset["train"], dataset["test"]
        num_labels = 4
    else:
        dataset = load_dataset(name)
        split_names = list(dataset.keys())
        train = dataset[split_names[0]]
        val = dataset[split_names[1] if len(split_names) > 1 else split_names[0]]
        labels = sorted({int(label) for label in train["label"]}) if "label" in train.column_names else []
        num_labels = len(labels) or num_labels
    train_rows = [{"text": str(row[text_field]), "label": int(row[label_field])} for row in train]
    val_rows = [{"text": str(row[text_field]), "label": int(row[label_field])} for row in val]
    return train_rows, val_rows, num_labels


def build_datasets(cfg: dict[str, Any], tokenizer):
    """把 examples 转成 HF Dataset（tokenized）。"""
    from datasets import Dataset

    train_rows, val_rows, num_labels = load_examples(cfg)
    data_cfg = cfg.get("data", {}) or {}
    max_length = int(data_cfg.get("max_length", 128))

    def tokenize(batch):
        return tokenizer(batch["text"], truncation=True, max_length=max_length, padding="max_length")

    train_ds = Dataset.from_list(train_rows).map(tokenize, batched=True)
    val_ds = Dataset.from_list(val_rows).map(tokenize, batched=True)
    columns = [name for name in ("input_ids", "attention_mask", "token_type_ids", "label") if name in train_ds.column_names]
    train_ds = train_ds.select_columns(columns)
    val_ds = val_ds.select_columns(columns)
    return train_ds, val_ds, num_labels
