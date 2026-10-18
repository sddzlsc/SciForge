"""Prompt 加载与渲染。

Prompt 以 Markdown 文件形式放在 ``sciforge/prompts/``，便于非程序员参与打磨。
"""

from __future__ import annotations

import re
from importlib.resources import files


def load_prompt(name: str) -> str:
    """读取 prompts/ 下的 md 文件；``name`` 可带或不带 .md 后缀。"""
    filename = name if name.endswith(".md") else f"{name}.md"
    resource = files("sciforge.prompts").joinpath(filename)
    return resource.read_text(encoding="utf-8")


def render_prompt(template: str, **variables: object) -> str:
    """把 ``{{ variable }}`` 替换为值（保留未匹配的占位符，避免 KeyError）。"""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, template)


def load_system_prompt(language: str = "zh") -> str:
    name = "system_en" if language.lower().startswith("en") else "system_zh"
    return load_prompt(name)


def load_stage_prompt(stage: str, language: str = "zh") -> str:
    suffix = "en" if language.lower().startswith("en") else "zh"
    try:
        return load_prompt(f"stages/{stage}_{suffix}")
    except (FileNotFoundError, ModuleNotFoundError):
        return load_prompt(f"stages/{stage}")


__all__ = ["load_prompt", "render_prompt", "load_system_prompt", "load_stage_prompt"]
