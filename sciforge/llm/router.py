"""LLM 路由：按角色（planner/executor/writer/reviewer/vision）选择 provider 与模型。"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from sciforge.config import Settings
from sciforge.core.types import ChatMessage, LLMResponse, Usage
from sciforge.llm.base import LLMError, LLMProvider
from sciforge.llm.mock import MockProvider
from sciforge.llm.openai_compat import OpenAICompatProvider


class _BoundProvider(LLMProvider):
    """把某个 provider 绑定到固定模型/角色。"""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        role: str,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        on_usage: Callable[[Usage], None] | None = None,
    ) -> None:
        self._provider = provider
        self.name = provider.name
        self.role = role
        self.model = model or getattr(provider, "model", "")
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._on_usage = on_usage

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        response = self._provider.chat(
            messages,
            tools=tools,
            model=model or self.model,
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
            stream=stream,
            on_delta=on_delta,
        )
        if self._on_usage:
            self._on_usage(response.usage)
        return response


class LLMRouter:
    """统一的 LLM 出口。

    - 按角色路由模型；
    - 累计 token / 成本；
    - ``force_mock=True`` 或环境变量 ``SCIFORGE_MOCK=1`` 时全部走离线 Mock。
    """

    def __init__(self, settings: Settings, *, force_mock: bool | None = None) -> None:
        self.settings = settings
        self.force_mock = (
            force_mock
            if force_mock is not None
            else os.environ.get("SCIFORGE_MOCK", "").lower() in ("1", "true", "yes")
        )
        self.usage = Usage()
        self._providers: dict[str, LLMProvider] = {}

    # ------------------------------------------------------------------
    def _make_provider(self, name: str) -> LLMProvider:
        if self.force_mock:
            return MockProvider(name=f"mock:{name}", model=f"{name}-mock")
        config = self.settings.llm.providers.get(name)
        if config is None:
            raise LLMError(
                f"未配置 LLM provider [{name}]。请在 ~/.sciforge/config.toml 添加 "
                f"[llm.providers.{name}]，或运行 `sciforge init` 生成配置。"
            )
        if config.type == "mock":
            return MockProvider(name=name, model=config.default_model)
        api_key = self.settings.resolve_api_key(name)
        return OpenAICompatProvider(name, config, api_key=api_key)

    def provider(self, name: str | None = None) -> LLMProvider:
        key = name or self.settings.llm.default
        if key not in self._providers:
            self._providers[key] = self._make_provider(key)
        return self._providers[key]

    def for_role(self, role: str = "executor") -> LLMProvider:
        role_cfg = self.settings.llm.roles.get(role)
        provider_name = self.settings.llm.provider_name_for(role)
        base = self.provider(provider_name)
        model = role_cfg.model if role_cfg and role_cfg.model else getattr(base, "model", None)
        return _BoundProvider(
            base,
            role=role,
            model=model,
            temperature=role_cfg.temperature if role_cfg else None,
            max_tokens=role_cfg.max_tokens if role_cfg else None,
            on_usage=lambda usage: self.usage.add(usage),
        )

    # ------------------------------------------------------------------
    def chat(
        self,
        role: str,
        messages: list[ChatMessage],
        **kwargs: Any,
    ) -> LLMResponse:
        return self.for_role(role).chat(messages, **kwargs)

    def complete(
        self,
        role: str,
        prompt: str,
        *,
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        return self.for_role(role).complete(prompt, system=system, **kwargs)

    # ------------------------------------------------------------------
    def check(self, role: str = "executor") -> dict[str, Any]:
        """doctor 用：探测某角色是否可用（不真正发起请求）。"""
        provider_name = self.settings.llm.provider_name_for(role)
        if self.force_mock:
            return {"ok": True, "provider": "mock", "model": "mock", "note": "离线 Mock 模式"}
        config = self.settings.llm.providers.get(provider_name)
        if config is None:
            return {"ok": False, "provider": provider_name, "error": "provider 未配置"}
        api_key = self.settings.resolve_api_key(provider_name)
        role_cfg = self.settings.llm.roles.get(role)
        return {
            "ok": bool(api_key),
            "provider": provider_name,
            "base_url": config.base_url,
            "model": (role_cfg.model if role_cfg and role_cfg.model else config.default_model),
            "api_key_source": config.api_key_env if not config.api_key else "config",
            "error": None if api_key else f"缺少 API key（{config.api_key_env or 'DEEPSEEK_API_KEY'}）",
        }


__all__ = ["LLMRouter", "LLMError"]
