"""Agent 运行时装配：事件总线 / 工具上下文 / LLM 路由 / Agent。"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sciforge.agent.loop import AgentLoop
from sciforge.agent.prompts import load_system_prompt
from sciforge.config import Settings, ensure_dirs, get_settings
from sciforge.core.events import EventBus
from sciforge.core.registry import ToolContext
from sciforge.llm.router import LLMRouter


def make_bus(handler: Callable | None = None) -> EventBus:
    bus = EventBus()
    if handler is not None:
        bus.subscribe(handler)
    return bus


def make_ctx(
    settings: Settings,
    *,
    project_dir: Path | None = None,
    bus: EventBus | None = None,
    session_id: str | None = None,
    extra: dict[str, Any] | None = None,
    approve: Callable[[str, dict], bool] | None = None,
) -> ToolContext:
    data_dir = ensure_dirs(settings)
    project = Path(project_dir or settings.project_dir or Path.cwd()).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    sid = session_id or f"s-{time.strftime('%Y%m%d-%H%M%S')}"
    session_dir = project / ".sciforge" / "sessions" / sid
    session_dir.mkdir(parents=True, exist_ok=True)

    def emit(kind: str, payload: dict[str, Any]) -> None:
        if bus is not None:
            bus.emit(kind, payload)

    return ToolContext(
        settings=settings,
        project_dir=project,
        data_dir=data_dir,
        session_dir=session_dir,
        emit=emit,
        approve=approve or (lambda name, args: True),
        extra=dict(extra or {}),
    )


def make_router(
    settings: Settings,
    *,
    mock: bool | None = None,
) -> LLMRouter:
    return LLMRouter(settings, force_mock=mock)


def make_agent(
    settings: Settings,
    *,
    project_dir: Path | None = None,
    session_id: str | None = None,
    bus: EventBus | None = None,
    router: LLMRouter | None = None,
    mock: bool | None = None,
    role: str = "executor",
    tools: list[str] | None = None,
    approval: str | None = None,
    max_steps: int | None = None,
    stream: bool = False,
    extra: dict[str, Any] | None = None,
) -> tuple[AgentLoop, ToolContext, LLMRouter]:
    settings = settings
    router = router or make_router(settings, mock=mock)
    ctx = make_ctx(settings, project_dir=project_dir, bus=bus, session_id=session_id, extra=extra)
    agent = AgentLoop(
        router,
        ctx,
        system_prompt=load_system_prompt(language=settings.agent.language),
        tools=tools,
        role=role,
        max_steps=max_steps or settings.agent.max_steps,
        approval=approval or settings.agent.approval,
        stream=stream,
        bus=bus,
        max_tool_output_chars=settings.agent.max_tool_output_chars,
        temperature=settings.agent.temperature,
    )
    return agent, ctx, router


def run_task(
    task: str,
    *,
    project_dir: Path | None = None,
    settings: Settings | None = None,
    mock: bool | None = None,
    tools: list[str] | None = None,
    stream: bool = False,
    on_event: Callable | None = None,
    role: str = "executor",
) -> Any:
    """一次性运行 agent（CLI / Pipeline 共用）。"""
    settings = settings or get_settings(project_dir)
    bus = make_bus(on_event)
    agent, ctx, _router = make_agent(
        settings,
        project_dir=project_dir,
        bus=bus,
        mock=mock,
        tools=tools,
        stream=stream,
        role=role,
    )
    return agent.run(task)


__all__ = ["make_bus", "make_ctx", "make_router", "make_agent", "run_task"]
