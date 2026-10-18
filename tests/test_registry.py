"""工具注册表测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.config import get_settings
from sciforge.core.registry import (
    BaseTool,
    ToolContext,
    ToolResult,
    get_tool,
    list_tools,
    register_tool,
    run_tool,
    tool_schemas,
)


class EchoTool(BaseTool):
    name = "test_echo"
    description = "回显输入（测试用）"
    tags = ("test",)
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def run(self, ctx: ToolContext, **kwargs) -> ToolResult:
        return ToolResult.success(str(kwargs.get("text", "")), data={"len": len(str(kwargs.get("text", "")))})


class BoomTool(BaseTool):
    name = "test_boom"
    description = "总是抛异常（测试用）"
    tags = ("test",)
    parameters = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext, **kwargs) -> ToolResult:
        raise RuntimeError("boom")


@pytest.fixture()
def ctx(sciforge_home: Path, project: Path) -> ToolContext:
    settings = get_settings(project)
    return ToolContext(
        settings=settings,
        project_dir=project,
        data_dir=sciforge_home,
        session_dir=project / ".sciforge" / "sessions" / "t",
    )


def test_register_and_schema() -> None:
    tool = EchoTool()
    register_tool(tool, replace=True)
    assert get_tool("test_echo") is tool
    schema = tool_schemas(["test_echo"])[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "test_echo"


def test_run_tool_success(ctx: ToolContext) -> None:
    register_tool(EchoTool(), replace=True)
    result = run_tool("test_echo", ctx, {"text": "你好"})
    assert result.ok and result.content == "你好"
    assert result.data["len"] == 2


def test_run_tool_unknown(ctx: ToolContext) -> None:
    result = run_tool("no_such_tool", ctx, {})
    assert not result.ok and "未知工具" in result.error


def test_run_tool_catches_exception(ctx: ToolContext) -> None:
    register_tool(BoomTool(), replace=True)
    result = run_tool("test_boom", ctx, {})
    assert not result.ok
    assert "boom" in (result.error or "")


def test_list_tools_filters_by_tag() -> None:
    register_tool(EchoTool(), replace=True)
    assert "test_echo" in [t.name for t in list_tools(["test"])]
    assert all("test" in t.tags for t in list_tools(["test"]))
