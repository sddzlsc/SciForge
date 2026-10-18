"""Agent Loop：ReAct 风格的「思考 → 调工具 → 观察 → 继续」循环。

与具体 UI 解耦：所有过程通过 EventBus 发出事件，CLI 用 Rich 渲染、Web 用 SSE 转发。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sciforge.core.events import EventBus
from sciforge.core.registry import ToolContext, list_tools, run_tool, tool_schemas
from sciforge.core.types import ChatMessage, LLMResponse, Usage
from sciforge.llm.base import LLMError
from sciforge.llm.router import LLMRouter


@dataclass
class AgentResult:
    content: str
    messages: list[ChatMessage]
    steps: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    stopped_reason: str = "stop"
    artifacts: list[str] = field(default_factory=list)
    session_path: Path | None = None
    error: str | None = None


class AgentLoop:
    def __init__(
        self,
        router: LLMRouter,
        ctx: ToolContext,
        *,
        system_prompt: str,
        tools: list[str] | None = None,
        role: str = "executor",
        max_steps: int = 30,
        approval: str = "auto",
        stream: bool = False,
        bus: EventBus | None = None,
        max_tool_output_chars: int = 12000,
        temperature: float | None = None,
    ) -> None:
        # 确保内置工具已注册（工具模块 import 时自注册）
        import sciforge.tools  # noqa: F401

        self.router = router
        self.ctx = ctx
        self.role = role
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.approval = approval
        self.stream = stream
        self.bus = bus
        self.max_tool_output_chars = max_tool_output_chars
        self.temperature = temperature

        available = list_tools()
        if tools is not None:
            wanted = set(tools)
            available = [t for t in available if t.name in wanted]
        self.tool_objects = {t.name: t for t in available}
        self.tool_specs = tool_schemas(self.tool_objects.keys())

    # ------------------------------------------------------------------
    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.ctx.emit(kind, payload)
        if self.bus is not None:
            self.bus.emit(kind, payload)

    def _check_approval(self, name: str, args: dict[str, Any]) -> bool:
        tool = self.tool_objects.get(name)
        if tool is None or not getattr(tool, "needs_approval", False):
            return True
        if self.approval in ("deny",):
            return False
        if self.approval == "auto":
            return True
        try:
            return bool(self.ctx.approve(name, args))
        except Exception:  # noqa: BLE001 - 审批回调失败按拒绝处理
            return False

    # ------------------------------------------------------------------
    def run(
        self,
        task: str,
        *,
        history: list[ChatMessage] | None = None,
        max_steps: int | None = None,
    ) -> AgentResult:
        session_id = self.ctx.session_dir.name
        messages: list[ChatMessage] = [ChatMessage(role="system", content=self.system_prompt)]
        if history:
            messages.extend(history)
        messages.append(ChatMessage(role="user", content=task))

        steps = 0
        tool_count = 0
        artifacts: list[str] = []
        final_content = ""
        stopped_reason = "max_steps"
        error: str | None = None
        limit = max_steps or self.max_steps

        self._emit("stage", {"stage": "agent_start", "task": task[:300]})

        for step in range(1, limit + 1):
            steps = step
            on_delta = None
            if self.stream:
                on_delta = lambda piece: self._emit("llm_delta", {"text": piece})  # noqa: E731
            try:
                response: LLMResponse = self.router.chat(
                    self.role,
                    messages,
                    tools=self.tool_specs or None,
                    temperature=self.temperature,
                    stream=self.stream and bool(on_delta),
                    on_delta=on_delta,
                )
            except LLMError as exc:
                error = str(exc)
                self._emit("error", {"message": error, "where": "llm"})
                stopped_reason = "llm_error"
                break

            assistant = ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls or None,
            )
            messages.append(assistant)
            self._emit(
                "assistant",
                {
                    "step": step,
                    "content": response.content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                },
            )

            if not response.tool_calls:
                final_content = response.content
                stopped_reason = "stop"
                break

            for call in response.tool_calls:
                tool_count += 1
                self._emit("tool_call", {"name": call.name, "arguments": call.arguments, "id": call.id})
                if call.name not in self.tool_objects:
                    result_text = f"[ERROR] 未知工具：{call.name}。可用工具：{', '.join(sorted(self.tool_objects))}"
                    self._emit("tool_result", {"name": call.name, "ok": False, "content": result_text})
                    messages.append(
                        ChatMessage(role="tool", content=result_text, tool_call_id=call.id, name=call.name)
                    )
                    continue

                if not self._check_approval(call.name, call.arguments):
                    denied = f"[DENIED] 用户拒绝执行工具 {call.name}。请换一种安全做法或询问用户。"
                    self._emit("tool_result", {"name": call.name, "ok": False, "content": denied})
                    messages.append(
                        ChatMessage(role="tool", content=denied, tool_call_id=call.id, name=call.name)
                    )
                    continue

                started = time.monotonic()
                result = run_tool(call.name, self.ctx, call.arguments)
                elapsed = round(time.monotonic() - started, 2)
                artifacts.extend(result.artifacts)
                payload = {
                    "name": call.name,
                    "ok": result.ok,
                    "content": result.content[:2000],
                    "error": result.error,
                    "elapsed": elapsed,
                    "artifacts": result.artifacts,
                }
                self._emit("tool_result", payload)
                text = result.to_llm_text()
                if len(text) > self.max_tool_output_chars:
                    text = text[: self.max_tool_output_chars] + "\n…（输出过长已截断，请用更精确的查询）"
                messages.append(
                    ChatMessage(role="tool", content=text, tool_call_id=call.id, name=call.name)
                )

        if stopped_reason == "max_steps" and not final_content:
            final_content = (
                f"已达到最大步数（{limit}），自动停止。以下是最后一条模型消息：\n"
                + next((m.content for m in reversed(messages) if m.role == "assistant" and m.content), "")
            )
            self._emit("error", {"message": "达到最大步数限制", "where": "agent"})

        result = AgentResult(
            content=final_content,
            messages=messages,
            steps=steps,
            tool_calls=tool_count,
            usage=self.router.usage,
            stopped_reason=stopped_reason,
            artifacts=sorted(set(artifacts)),
            session_path=None,
            error=error,
        )
        self._save_session(session_id, task, result)
        result.session_path = self.ctx.session_dir / "transcript.json"
        self._emit(
            "stage",
            {
                "stage": "agent_end",
                "steps": steps,
                "tool_calls": tool_count,
                "stopped_reason": stopped_reason,
                "usage": result.usage.as_dict(),
            },
        )
        return result

    # ------------------------------------------------------------------
    def _save_session(self, session_id: str, task: str, result: AgentResult) -> None:
        try:
            self.ctx.session_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "session_id": session_id,
                "task": task,
                "created_at": time.time(),
                "steps": result.steps,
                "tool_calls": result.tool_calls,
                "stopped_reason": result.stopped_reason,
                "usage": result.usage.as_dict(),
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "tool_call_id": m.tool_call_id,
                        "name": m.name,
                        "tool_calls": [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in (m.tool_calls or [])
                        ],
                    }
                    for m in result.messages
                ],
                "artifacts": result.artifacts,
            }
            (self.ctx.session_dir / "transcript.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass


def new_session_id(prefix: str = "s") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


__all__ = ["AgentLoop", "AgentResult", "new_session_id"]
