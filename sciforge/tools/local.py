"""本地文件与命令工具（Agent 的手脚）。

安全边界：
- 写文件只允许在项目目录、会话目录、SciForge 数据目录内；
- 拒绝写入明显的密钥文件；
- shell 需要审批（AgentLoop 根据 approval 策略处理）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool
from sciforge.core.types import ToolResult as _ToolResult

MAX_READ_CHARS = 60_000
MAX_WRITE_CHARS = 400_000

_SECRET_NAMES = {".env", "id_rsa", "id_ed25519", ".credentials.yaml", "credentials", "config.local.toml"}


def _is_secret(path: Path) -> bool:
    name = path.name.lower()
    return name in _SECRET_NAMES or name.endswith(".pem") or name.endswith(".key")


def _resolve_in_scope(ctx: ToolContext, raw: str, *, for_write: bool = False) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = ctx.project_dir / path
    path = path.resolve()
    allowed = [ctx.project_dir.resolve(), ctx.session_dir.resolve(), ctx.data_dir.resolve()]
    if for_write and not any(_is_within(path, base) for base in allowed):
        raise PermissionError(
            f"拒绝写入项目/会话/数据目录之外的路径：{path}。如需写入请先征得用户同意。"
        )
    if _is_secret(path):
        raise PermissionError(f"拒绝访问密钥类文件：{path}")
    return path


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "读取项目内文本文件（支持 offset/limit 行范围）。修改文件前必须先读。"
    tags = ("fs",)
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对项目目录或绝对路径）"},
            "offset": {"type": "integer", "description": "起始行号，1-based，默认 1"},
            "limit": {"type": "integer", "description": "最多读取行数，默认 2000"},
        },
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        path = _resolve_in_scope(ctx, self.require(kwargs, "path"))
        if not path.exists():
            return ToolResult.failure(f"文件不存在：{path}")
        if path.is_dir():
            return ToolResult.failure(f"这是目录，请用 list_dir：{path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n…（文件过长已截断）"
        offset = max(1, int(kwargs.get("offset") or 1))
        limit = max(1, min(int(kwargs.get("limit") or 2000), 6000))
        lines = text.splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        body = "\n".join(f"{i + offset:>5}| {line}" for i, line in enumerate(selected))
        return ToolResult.success(
            f"{path}\n{body}",
            data={"path": str(path), "lines": len(lines), "shown": len(selected)},
        )


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "在项目目录内创建或整体覆盖写文件（自动建父目录）。研究笔记、配置、脚本、草稿都落盘。"
    tags = ("fs",)
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标路径（相对项目目录）"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        path = _resolve_in_scope(ctx, self.require(kwargs, "path"), for_write=True)
        content = str(self.require(kwargs, "content"))
        if len(content) > MAX_WRITE_CHARS:
            return ToolResult.failure("内容过长，请拆分为多个文件写入")
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        action = "覆盖" if existed else "创建"
        lines = content.count("\n") + 1
        return ToolResult.success(
            f"已{action} {path}（{lines} 行，{len(content)} 字符）",
            data={"path": str(path), "existed": existed, "lines": lines},
            artifacts=[str(path)],
        )


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "对文件做精确字符串替换（old_string 必须唯一出现）。适合小改动，避免整体重写。"
    tags = ("fs",)
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "要被替换的原文（需唯一）"},
            "new_string": {"type": "string", "description": "替换后的内容"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        path = _resolve_in_scope(ctx, self.require(kwargs, "path"), for_write=True)
        if not path.exists():
            return ToolResult.failure(f"文件不存在：{path}")
        old = str(self.require(kwargs, "old_string"))
        new = str(kwargs.get("new_string") or "")
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return ToolResult.failure("未找到 old_string，请先 read_file 确认原文")
        if count > 1 and not kwargs.get("replace_all"):
            return ToolResult.failure(f"old_string 出现 {count} 次，请提供更长上下文或加 replace_all=true")
        text = text.replace(old, new) if kwargs.get("replace_all") else text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        return ToolResult.success(
            f"已更新 {path}（替换 {count if kwargs.get('replace_all') else 1} 处）",
            artifacts=[str(path)],
        )


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "列出目录内容（可带 glob 过滤），用于了解项目结构。"
    tags = ("fs",)
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录，默认项目根目录"},
            "pattern": {"type": "string", "description": "如 *.py、**/*.md"},
            "max_entries": {"type": "integer", "default": 200},
        },
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        raw = kwargs.get("path") or "."
        path = _resolve_in_scope(ctx, raw)
        if not path.exists():
            return ToolResult.failure(f"目录不存在：{path}")
        if path.is_file():
            return ToolResult.success(f"{path} (文件, {path.stat().st_size} bytes)")
        pattern = kwargs.get("pattern") or "*"
        limit = int(kwargs.get("max_entries") or 200)
        entries = []
        base = path
        for item in sorted(base.glob(pattern))[:limit]:
            rel = item.relative_to(base) if _is_within(item, base) else item
            kind = "/" if item.is_dir() else ""
            size = "" if item.is_dir() else f"  {item.stat().st_size}B"
            entries.append(f"{rel}{kind}{size}")
        body = "\n".join(entries) if entries else "（空）"
        return ToolResult.success(f"{path}\n{body}", data={"count": len(entries)})


class ShellTool(BaseTool):
    name = "shell"
    description = (
        "在本地项目目录执行 shell 命令（需要审批）。用于运行本地脚本、git、pip、编译 LaTeX 等。"
        "长任务请重定向到日志并后台运行。不要执行破坏性命令。"
    )
    tags = ("fs", "shell")
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "相对项目目录，默认项目根"},
            "timeout": {"type": "integer", "description": "秒，默认 120，最大 1800"},
        },
        "required": ["command"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        command = str(self.require(kwargs, "command"))
        cwd = _resolve_in_scope(ctx, kwargs.get("cwd") or ".")
        timeout = max(1, min(int(kwargs.get("timeout") or 120), 1800))
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd if cwd.is_dir() else ctx.project_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"命令超时（{timeout}s）。长任务请用 nohup/tmux 后台运行。")
        output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        output = output.strip() or "(无输出)"
        if len(output) > 20_000:
            output = output[:20_000] + "\n…（输出截断）"
        ok = proc.returncode == 0
        header = f"$ {command}\nexit={proc.returncode}"
        return _ToolResult(
            ok=ok,
            content=f"{header}\n{output}",
            data={"exit_code": proc.returncode, "cwd": str(cwd)},
            error=None if ok else f"命令退出码 {proc.returncode}",
        )


class ProjectTreeTool(BaseTool):
    name = "project_tree"
    description = "生成项目目录树（忽略 .git/.venv/__pycache__ 等），快速了解当前项目全貌。"
    tags = ("fs",)
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "默认项目根"},
            "depth": {"type": "integer", "default": 3},
        },
    }

    IGNORE = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache", ".sciforge"}

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        root = _resolve_in_scope(ctx, kwargs.get("path") or ".")
        depth = max(1, min(int(kwargs.get("depth") or 3), 6))
        lines: list[str] = [f"{root.name}/"]

        def walk(directory: Path, prefix: str, level: int) -> None:
            if level > depth:
                return
            try:
                items = sorted(
                    [p for p in directory.iterdir() if p.name not in self.IGNORE],
                    key=lambda p: (p.is_file(), p.name),
                )
            except OSError:
                return
            for index, item in enumerate(items):
                last = index == len(items) - 1
                connector = "└── " if last else "├── "
                marker = "/" if item.is_dir() else ""
                lines.append(f"{prefix}{connector}{item.name}{marker}")
                if item.is_dir():
                    walk(item, prefix + ("    " if last else "│   "), level + 1)

        walk(root, "", 1)
        return ToolResult.success("\n".join(lines[:400]))


register_tool(ReadFileTool())
register_tool(WriteFileTool())
register_tool(EditFileTool())
register_tool(ListDirTool())
register_tool(ShellTool())
register_tool(ProjectTreeTool())


__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListDirTool",
    "ShellTool",
    "ProjectTreeTool",
]
