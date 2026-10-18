"""核心数据结构：Tool / Chat / LLM 结果。

这些类型是 Harness 内部以及 LLM 协议之间的公共语汇，签名稳定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolResult:
    """一个 Tool 执行后的统一结果。"""

    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    display: str | None = None

    @classmethod
    def success(
        cls,
        content: str,
        *,
        data: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
        display: str | None = None,
    ) -> ToolResult:
        return cls(
            ok=True,
            content=content,
            data=data or {},
            artifacts=artifacts or [],
            display=display,
        )

    @classmethod
    def failure(cls, error: str, *, content: str | None = None) -> ToolResult:
        return cls(ok=False, content=content or f"工具执行失败：{error}", error=error)

    def to_llm_text(self) -> str:
        """转换成回传给模型的文本（控制长度，保留关键信息）。"""
        body = self.content.strip()
        if not self.ok and self.error:
            body = f"[ERROR] {self.error}\n{body}".strip()
        if self.artifacts:
            body += "\n[artifacts]\n" + "\n".join(self.artifacts)
        return body


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        """OpenAI / DeepSeek Chat Completions 消息格式。"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": _json_dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, other: Usage) -> Usage:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cost_usd += other.cost_usd
        self.calls += other.calls or 1
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
        }


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    model: str = ""
    raw: dict[str, Any] | None = None


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "Role",
    "ToolResult",
    "ToolCall",
    "ChatMessage",
    "Usage",
    "LLMResponse",
]
