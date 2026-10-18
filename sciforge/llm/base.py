"""LLM Provider 抽象。

所有 Provider 都是同步接口（科研批处理场景足够，且便于在 CLI/Web/线程中复用）。
任何 OpenAI 兼容服务都能通过 :class:`sciforge.llm.openai_compat.OpenAICompatProvider` 接入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from sciforge.core.types import ChatMessage, LLMResponse


class LLMError(RuntimeError):
    """LLM 调用失败（网络/鉴权/协议），消息面向用户可读。"""


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
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
    ) -> LLMResponse:  # pragma: no cover - 抽象方法
        raise NotImplementedError

    # 便捷方法：单轮补全，返回纯文本
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))
        response = self.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.content


__all__ = ["LLMProvider", "LLMError"]
