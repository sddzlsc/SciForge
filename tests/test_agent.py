"""Agent Loop 测试：脚本化 tool call → 工具执行 → 最终回答。"""

from __future__ import annotations

from pathlib import Path

from sciforge.agent.loop import AgentLoop
from sciforge.agent.runtime import make_ctx, make_router
from sciforge.config import get_settings
from sciforge.core.events import EventBus
from sciforge.core.types import LLMResponse, ToolCall, Usage
from sciforge.llm.mock import MockProvider


def _tool_response() -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "notes/a.md", "content": "# 笔记"})],
        usage=Usage(total_tokens=3, calls=1),
    )


def test_agent_executes_tool_and_finishes(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    mock = MockProvider(scripts=[_tool_response(), "任务完成，已写入 notes/a.md。"])
    router = make_router(settings, mock=True)
    router._providers["deepseek"] = mock  # 强制使用脚本化 mock
    bus = EventBus()
    events: list[tuple[str, dict]] = []
    bus.subscribe(lambda e: events.append((e.kind, e.payload)))
    ctx = make_ctx(settings, project_dir=project, bus=bus, session_id="test-agent")
    agent = AgentLoop(
        router,
        ctx,
        system_prompt="你是测试助手",
        tools=["write_file"],
        max_steps=4,
        bus=bus,
    )
    result = agent.run("写一条笔记")
    assert result.stopped_reason == "stop"
    assert "任务完成" in result.content
    assert (project / "notes" / "a.md").read_text(encoding="utf-8") == "# 笔记"
    assert result.tool_calls == 1
    assert any(kind == "tool_call" for kind, _ in events)
    assert result.session_path is not None and result.session_path.exists()


def test_agent_max_steps(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    loop_response = LLMResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name="project_tree", arguments={})],
        usage=Usage(calls=1),
    )
    mock = MockProvider(scripts=[loop_response] * 10)
    router = make_router(settings, mock=True)
    router._providers["deepseek"] = mock
    ctx = make_ctx(settings, project_dir=project, session_id="test-max")
    agent = AgentLoop(router, ctx, system_prompt="x", tools=["project_tree"], max_steps=3)
    result = agent.run("循环")
    assert result.stopped_reason == "max_steps"
    assert result.steps == 3


def test_agent_unknown_tool_is_reported(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    bad = LLMResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name="no_such_tool", arguments={})],
        usage=Usage(calls=1),
    )
    mock = MockProvider(scripts=[bad, "抱歉，工具不存在。"])
    router = make_router(settings, mock=True)
    router._providers["deepseek"] = mock
    ctx = make_ctx(settings, project_dir=project, session_id="test-bad")
    agent = AgentLoop(router, ctx, system_prompt="x", tools=["project_tree"], max_steps=3)
    result = agent.run("x")
    assert result.content
    assert any("未知工具" in m.content for m in result.messages if m.role == "tool")


def test_agent_unlimited_steps_completes(sciforge_home: Path, project: Path) -> None:
    """max_steps=0 表示无上限：模型自己停止时正常结束，而不是被步数截断。"""
    settings = get_settings(project)
    mock = MockProvider(scripts=[_tool_response(), "任务完成。"])
    router = make_router(settings, mock=True)
    router._providers["deepseek"] = mock
    ctx = make_ctx(settings, project_dir=project, session_id="test-unlimited")
    agent = AgentLoop(router, ctx, system_prompt="x", tools=["write_file"], max_steps=0)
    result = agent.run("写一条笔记")
    assert result.stopped_reason == "stop"
    assert result.steps == 2
    assert "任务完成" in result.content


def test_make_agent_respects_configured_and_unlimited_steps(
    sciforge_home: Path, project: Path
) -> None:
    from sciforge.agent.runtime import make_agent

    settings = get_settings(project)
    settings.agent.max_steps = 7
    agent, _ctx, _router = make_agent(settings, project_dir=project, mock=True)
    assert agent.max_steps == 7

    settings.agent.max_steps = 0
    agent, _ctx, _router = make_agent(settings, project_dir=project, mock=True)
    assert agent.max_steps == 0

    # 显式 override 优先于配置
    agent, _ctx, _router = make_agent(settings, project_dir=project, mock=True, max_steps=12)
    assert agent.max_steps == 12
