"""模型：基座 + LoRA（peft），可选 4bit 量化。"""

from __future__ import annotations

from typing import Any


def build_tokenizer(cfg: dict[str, Any]):
    from transformers import AutoTokenizer

    model_cfg = cfg.get("model", {}) or {}
    tokenizer = AutoTokenizer.from_pretrained(
        str(model_cfg.get("name")),
        trust_remote_code=bool(model_cfg.get("trust_remote_code", False)),
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def build_model(cfg: dict[str, Any]):
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    model_cfg = cfg.get("model", {}) or {}
    quant_cfg = cfg.get("quantization", {}) or {}
    kwargs: dict[str, Any] = {
        "trust_remote_code": bool(model_cfg.get("trust_remote_code", False)),
        "torch_dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
    }
    if bool(quant_cfg.get("load_in_4bit")):
        compute = str(quant_cfg.get("bnb_4bit_compute_dtype", "bfloat16"))
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=getattr(torch, compute, torch.float16),
        )
        kwargs["device_map"] = "auto"
    model = AutoModelForCausalLM.from_pretrained(str(model_cfg.get("name")), **kwargs)
    if bool(model_cfg.get("lora", True)):
        from peft import LoraConfig, get_peft_model

        lora_config = LoraConfig(
            r=int(model_cfg.get("lora_r", 16)),
            lora_alpha=int(model_cfg.get("lora_alpha", 32)),
            lora_dropout=float(model_cfg.get("lora_dropout", 0.05)),
            target_modules=list(model_cfg.get("lora_target_modules") or ["q_proj", "v_proj"]),
            task_type="CAUSAL_LM",
            bias="none",
        )
        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()
    return model
