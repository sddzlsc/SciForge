"""Tool 注册表与执行上下文。

工具作者只需：

    from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool

    class MyTool(BaseTool):
        name = "my_tool"
        description = "什么时候该用、返回什么"
        tags = ("fs",)
        parameters = {...JSON Schema...}

        def run(self, ctx: ToolContext, **kwargs) -> ToolResult:
            ...
            return ToolResult.success("...", data={...})

    register_tool(MyTool())     # 模块被 import 时自注册
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .types import ToolResult

if TYPE_CHECKING:  # pragma: no cover
    from sciforge.config import Settings


@dataclass
class ToolContext:
    """一次 Tool 调用可用的上下文。"""

    settings: Settings
    project_dir: Path
    data_dir: Path
    session_dir: Path
    emit: Callable[[str, dict[str, Any]], Any] = lambda kind, payload: None
    approve: Callable[[str, dict[str, Any]], bool] = lambda name, args: True
    extra: dict[str, Any] = field(default_factory=dict)

    def log(self, message: str, level: str = "info") -> None:
        self.emit("log", {"level": level, "message": message})

    def artifact(self, name: str) -> Path:
        path = self.session_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def skills_dir(self) -> Path:
        return self.data_dir / "skills"

    def get_server(self, name_or_none: str | None):
        """按名字取 GPU 服务器配置；name 为空时取默认（第一个）。"""
        servers = getattr(self.settings, "servers", []) or []
        if not servers:
            return None
        if not name_or_none:
            return servers[0]
        for server in servers:
            if server.name == name_or_none:
                return server
        return None


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]
    tags: tuple[str, ...]
    requires: tuple[str, ...]

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult: ...


class BaseTool(ABC):
    """工具基类，提供 schema 与统一的参数校验。"""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    tags: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    # 若为 True，Agent 调用前需要人类审批（危险操作）
    needs_approval: bool = False

    @abstractmethod
    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    # ---- 供子类使用的小工具 ----
    @staticmethod
    def require(kwargs: dict[str, Any], key: str) -> Any:
        value = kwargs.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"缺少必填参数: {key}")
        return value

    @staticmethod
    def as_path(value: Any, base: Path | None = None) -> Path:
        path = Path(str(value)).expanduser()
        if not path.is_absolute() and base is not None:
            path = base / path
        return path

    @staticmethod
    def dumps(obj: Any, limit: int = 8000) -> str:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
        return text if len(text) <= limit else text[:limit] + "\n…（已截断）"


_REGISTRY: dict[str, BaseTool] = {}


def register_tool(tool: BaseTool, *, replace: bool = False) -> BaseTool:
    if not tool.name:
        raise ValueError(f"{type(tool).__name__} 必须定义 name")
    if tool.name in _REGISTRY and not replace:
        raise ValueError(f"工具名重复注册: {tool.name}")
    _REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> BaseTool | None:
    return _REGISTRY.get(name)


def list_tools(tags: Iterable[str] | None = None) -> list[BaseTool]:
    tools = list(_REGISTRY.values())
    if tags:
        wanted = set(tags)
        tools = [t for t in tools if wanted & set(t.tags)]
    return sorted(tools, key=lambda t: t.name)


def tool_schemas(names: Iterable[str] | None = None) -> list[dict[str, Any]]:
    if names is None:
        tools = list_tools()
    else:
        tools = []
        for name in names:
            tool = get_tool(name)
            if tool is not None:
                tools.append(tool)
    return [t.schema() for t in tools]


def run_tool(name: str, ctx: ToolContext, arguments: dict[str, Any] | None = None) -> ToolResult:
    tool = get_tool(name)
    if tool is None:
        return ToolResult.failure(f"未知工具: {name}")
    args = dict(arguments or {})
    try:
        return tool.run(ctx, **args)
    except TypeError as exc:
        return ToolResult.failure(f"工具 {name} 参数错误: {exc}")
    except Exception as exc:  # 工具失败不应打断 agent loop
        return ToolResult.failure(f"工具 {name} 执行异常: {type(exc).__name__}: {exc}")


def clear_registry() -> None:
    """仅测试使用。"""
    _REGISTRY.clear()


__all__ = [
    "Tool",
    "BaseTool",
    "ToolContext",
    "ToolResult",
    "register_tool",
    "get_tool",
    "list_tools",
    "tool_schemas",
    "run_tool",
]
