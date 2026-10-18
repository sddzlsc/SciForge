"""模型：HF AutoModelForSequenceClassification 封装。"""

from __future__ import annotations

from typing import Any


def build_model(cfg: dict[str, Any], num_labels: int):
    from transformers import AutoModelForSequenceClassification

    model_cfg = cfg.get("model", {}) or {}
    name = str(model_cfg.get("name", "distilbert-base-uncased"))
    dropout = float(model_cfg.get("dropout", 0.1))
    return AutoModelForSequenceClassification.from_pretrained(
        name,
        num_labels=num_labels,
        hidden_dropout_prob=dropout,
        attention_probs_dropout_prob=dropout,
        ignore_mismatched_sizes=True,
    )


def build_tokenizer(cfg: dict[str, Any]):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(str((cfg.get("model", {}) or {}).get("name", "distilbert-base-uncased")))
