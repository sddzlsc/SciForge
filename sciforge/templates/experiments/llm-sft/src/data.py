"""SFT 数据：JSONL 指令/对话数据 → tokenized dataset。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _normalize(record: dict[str, Any]) -> list[dict[str, str]]:
    if "messages" in record and isinstance(record["messages"], list):
        return [
            {"role": str(message.get("role", "user")), "content": str(message.get("content", ""))}
            for message in record["messages"]
        ]
    if "output" in record:
        prompt = str(record.get("instruction", ""))
        if record.get("input"):
            prompt += "\n" + str(record["input"])
        return [
            {"role": "user", "content": prompt.strip()},
            {"role": "assistant", "content": str(record["output"])},
        ]
    if "prompt" in record and "response" in record:
        return [
            {"role": "user", "content": str(record["prompt"])},
            {"role": "assistant", "content": str(record["response"])},
        ]
    raise ValueError(f"无法识别的 SFT 数据记录：{list(record)[:8]}")


def load_records(path: str | Path) -> list[list[dict[str, str]]]:
    file = Path(path).expanduser()
    if not file.exists():
        raise SystemExit(
            f"找不到 SFT 数据 {file}。请准备 JSONL（messages 或 instruction/output 格式），"
            "或先运行 `bash scripts/smoke.sh` 做离线自检。"
        )
    records: list[list[dict[str, str]]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(_normalize(json.loads(line)))
    if not records:
        raise SystemExit(f"SFT 数据为空：{file}")
    return records


def build_datasets(cfg: dict[str, Any], tokenizer):
    """把对话记录编码成 input_ids/labels（只对 assistant 片段计算 loss）。"""
    from datasets import Dataset

    data_cfg = cfg.get("data", {}) or {}
    records = load_records(str(data_cfg.get("train_file", "./data/train.jsonl")))
    max_length = int(data_cfg.get("max_length", 1024))

    def encode(messages: list[dict[str, str]]) -> dict[str, list[int]]:
        input_ids: list[int] = []
        labels: list[int] = []
        for message in messages:
            content = str(message.get("content", ""))
            if not content:
                continue
            if hasattr(tokenizer, "apply_chat_template") and message is messages[0]:
                ids = tokenizer.apply_chat_template([message], tokenize=True, add_generation_prompt=False)
            else:
                ids = tokenizer(content, add_special_tokens=False)["input_ids"]
            input_ids += list(ids)
            labels += list(ids) if message.get("role") == "assistant" else [-100] * len(ids)
        return {"input_ids": input_ids[:max_length], "labels": labels[:max_length], "attention_mask": [1] * min(len(input_ids), max_length)}

    rows = [encode(messages) for messages in records]
    dataset = Dataset.from_list(rows)
    ratio = float(data_cfg.get("val_ratio", 0.02))
    split = dataset.train_test_split(test_size=max(1, int(len(dataset) * ratio)), seed=int(cfg.get("seed", 42)))
    return split["train"], split["test"]
