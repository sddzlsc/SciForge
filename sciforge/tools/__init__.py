"""工具包：import 本包即完成全部内置工具的注册。

各工具模块在 import 时调用 ``register_tool(...)`` 自注册。并行开发期间某个模块
尚未落地时只记录 import_errors，不阻塞其它工具（`sciforge doctor` 会展示）。
"""

from __future__ import annotations

import importlib
from typing import Any

# 新增工具模块时只改这里
_TOOL_MODULES = [
    "local",          # read/write/edit/list/shell/tree
    "literature",     # 文献检索/下载
    "paper_reader",   # 论文精读/笔记
    "remote",         # SSH / GPU / 远程执行
    "experiments",    # 实验脚手架 / 启动 / 结果
    "figures",        # 顶会级图
    "writing",        # 论文写作
    "skills",         # Skill 加载与检索
    "ideation",       # 创新点
]

import_errors: dict[str, str] = {}

for _module in _TOOL_MODULES:
    try:
        importlib.import_module(f"sciforge.tools.{_module}")
    except ModuleNotFoundError as _exc:  # 允许并行开发期缺模块
        import_errors[_module] = f"模块未实现: {_exc}"
    except Exception as _exc:  # noqa: BLE001
        import_errors[_module] = f"{type(_exc).__name__}: {_exc}"

from sciforge.core.registry import (  # noqa: E402
    get_tool,
    list_tools,
    register_tool,
    run_tool,
    tool_schemas,
)

__all__ = [
    "get_tool",
    "list_tools",
    "register_tool",
    "run_tool",
    "tool_schemas",
    "import_errors",
    "tool_health",
]


def tool_health() -> dict[str, Any]:
    """给 doctor / Web 用的工具健康信息。"""
    tools = list_tools()
    return {
        "count": len(tools),
        "by_tag": _count_by_tag(tools),
        "names": [t.name for t in tools],
        "import_errors": import_errors,
    }


def _count_by_tag(tools: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tool in tools:
        for tag in getattr(tool, "tags", ()):
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items()))
