"""图工具：``figure_architecture`` / ``figure_plot`` / ``figure_table``。

给 Agent 用的三个入口，统一：
- 参数 ``spec`` 既可以是 dict，也可以是 YAML/JSON 文件路径；
- 产物默认写到 ``<project>/figures/``，同时把 spec 落盘同目录（可复现）；
- 返回 ``ToolResult.artifacts``（绝对路径）与结构化 ``data``（含降级说明）。

CLI：``add_cli(app)`` 挂 ``sciforge fig arch|plot|table|templates``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import typer

from sciforge.config import get_settings
from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool
from sciforge.figures import arch, plots, tables

_TAGS = ("figure",)


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------


def _slug(text: str, fallback: str = "figure") -> str:
    value = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff_-]+", "_", str(text)).strip("_")
    return value[:48] or fallback


def _load_spec_arg(ctx: ToolContext, raw: Any) -> dict[str, Any]:
    """spec 参数：dict 直接返回；str/Path 视为 spec 文件。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, Path)):
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = ctx.project_dir / path
        loader = arch.load_spec
        return loader(path)
    raise ValueError("spec 必须是 dict 或 spec 文件路径（YAML/JSON）")


def _resolve_out(ctx: ToolContext, raw: Any, default_name: str) -> Path:
    if raw:
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = ctx.project_dir / path
        return path
    target = ctx.project_dir / "figures" / default_name
    return target


def _formats_arg(ctx: ToolContext, raw: Any) -> list[str]:
    if raw:
        if isinstance(raw, str):
            return [part.strip().lstrip(".") for part in raw.split(",") if part.strip()]
        return [str(item).strip().lstrip(".") for item in raw]
    configured = getattr(ctx.settings.figures, "formats", None) or ["svg", "pdf", "png"]
    return list(configured)


def _artifact_paths(paths: list[Path]) -> list[str]:
    return [str(path.resolve()) for path in paths]


def _summarize_artifacts(paths: list[Path]) -> str:
    return "、".join(path.name for path in paths)


# ---------------------------------------------------------------------------
# Tool: 模型架构图
# ---------------------------------------------------------------------------


class FigureArchitectureTool(BaseTool):
    name = "figure_architecture"
    description = (
        "画论文级模型架构图（Figure 1 那种）：输入声明式 spec（YAML/JSON 或 dict），"
        "输出白底、8pt、Okabe-Ito 色盲友好、圆角模块 + 细箭头 + 虚线分组的高质感 SVG/PDF/PNG。"
        "支持横向 pipeline 与纵向 block、tensor 扁条、注释、图例；图例与 spec 会一起落盘。"
        "什么时候用：需要画/改模型结构图、训练流程图、多模态 pipeline 时。"
    )
    tags = _TAGS
    requires = ("matplotlib",)
    parameters = {
        "type": "object",
        "properties": {
            "spec": {
                "type": ["object", "string"],
                "description": "架构图 spec（dict）或 YAML/JSON 文件路径",
            },
            "out": {
                "type": "string",
                "description": "输出路径（默认 figures/arch_<title>.svg）",
            },
            "formats": {
                "type": ["string", "array"],
                "description": "输出格式，如 'svg,pdf,png'；缺省用项目 figures.formats 配置",
                "items": {"type": "string"},
            },
            "dpi": {"type": "integer", "description": "PNG 分辨率，默认 300"},
        },
        "required": ["spec"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            spec = _load_spec_arg(ctx, self.require(kwargs, "spec"))
            title = str(spec.get("title") or "arch")
            out = _resolve_out(ctx, kwargs.get("out"), f"{_slug(title)}.svg")
            formats = _formats_arg(ctx, kwargs.get("formats") or spec.get("formats"))
            ctx.log(f"渲染模型架构图：{title} → {out.name}")
            result = arch.render_architecture_full(
                spec,
                out,
                formats=formats,
                dpi=int(kwargs.get("dpi") or 0) or None,
            )
        except Exception as exc:  # noqa: BLE001 - 工具失败不打断 agent
            return ToolResult.failure(
                f"架构图渲染失败：{type(exc).__name__}: {exc}",
                content=(
                    "请检查 spec：nodes 至少一个节点、edges 的 from/to 必须存在、"
                    "groups 的 row/col 为整数。也可以先用内置模板："
                    "`sciforge fig templates`。"
                ),
            )
        content = (
            f"已渲染架构图《{spec.get('title') or '未命名'}》：{_summarize_artifacts(result.artifacts)}。"
            f"{result.note}"
        )
        if result.warnings:
            content += "\n提示：" + "；".join(result.warnings)
        return ToolResult.success(
            content,
            data=result.as_dict(),
            artifacts=_artifact_paths(result.artifacts),
        )


# ---------------------------------------------------------------------------
# Tool: 数据图
# ---------------------------------------------------------------------------


class FigurePlotTool(BaseTool):
    name = "figure_plot"
    description = (
        "画论文级实验数据图：line/bar/scatter/radar/heatmap，支持误差带、显著性星标、多子图、"
        "NeurIPS/CVPR 风格（无顶右边框、tick 朝内、色盲配色、PDF 字体嵌入）。"
        "输入 spec（dict 或 YAML/JSON），输出 PDF/SVG/PNG 到 figures/。"
        "什么时候用：实验曲线、消融柱状图、混淆矩阵、雷达对比图。"
    )
    tags = _TAGS
    requires = ("matplotlib",)
    parameters = {
        "type": "object",
        "properties": {
            "spec": {
                "type": ["object", "string"],
                "description": "数据图 spec（dict）或 YAML/JSON 文件路径",
            },
            "out": {"type": "string", "description": "输出路径（默认 figures/plot_<title>.pdf）"},
            "formats": {
                "type": ["string", "array"],
                "description": "输出格式，如 'pdf,svg,png'；缺省用 figures.formats 配置",
            },
            "style": {"type": "string", "description": "neurips | cvpr | iclr | ieee"},
        },
        "required": ["spec"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            spec = _load_spec_arg(ctx, self.require(kwargs, "spec"))
            title = str(spec.get("title") or spec.get("kind") or "plot")
            out = _resolve_out(ctx, kwargs.get("out"), f"{_slug(title)}.pdf")
            formats = _formats_arg(ctx, kwargs.get("formats"))
            spec.setdefault("formats", formats)
            ctx.log(f"渲染数据图：{title} → {out.name}")
            result = plots.render_plot_full(
                spec,
                out,
                style=kwargs.get("style") or spec.get("style"),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"数据图渲染失败：{type(exc).__name__}: {exc}",
                content=(
                    "请检查 spec.kind 是否为 line|bar|scatter|radar|heatmap，"
                    "series 的 x/y 长度是否一致；热图需要 matrix。"
                ),
            )
        content = (
            f"已渲染数据图《{spec.get('title') or spec.get('kind')}》："
            f"{_summarize_artifacts(result.artifacts)}（{result.style} 风格）。{result.note}"
        )
        if result.warnings:
            content += "\n提示：" + "；".join(result.warnings)
        return ToolResult.success(
            content,
            data=result.as_dict(),
            artifacts=_artifact_paths(result.artifacts),
        )


# ---------------------------------------------------------------------------
# Tool: 三线表
# ---------------------------------------------------------------------------


class FigureTableTool(BaseTool):
    name = "figure_table"
    description = (
        "生成 booktabs 三线表（LaTeX）：最佳加粗、次佳下划线、多数据集列分组 \\multicolumn + \\cmidrule、"
        "caption/label/行分组；同时可输出 Markdown。输入 spec（dict 或 YAML/JSON），"
        "默认写到 figures/tables/<label>.tex。什么时候用：论文主结果表、消融表。"
    )
    tags = _TAGS
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "spec": {
                "type": ["object", "string"],
                "description": "表格 spec（dict）或 YAML/JSON 文件路径",
            },
            "out": {"type": "string", "description": "输出 .tex 路径（默认 figures/tables/<label>.tex）"},
            "markdown": {"type": "boolean", "description": "是否同时输出 .md（默认 true）"},
        },
        "required": ["spec"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            spec = _load_spec_arg(ctx, self.require(kwargs, "spec"))
            label = str(spec.get("label") or "table")
            name = _slug(label.replace("tab:", ""), "table")
            out = _resolve_out(ctx, kwargs.get("out"), f"tables/{name}.tex")
            latex = tables.render_latex_table(spec)
            markdown = tables.render_markdown_table(spec)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(latex + "\n", encoding="utf-8")
            artifacts = [out]
            if kwargs.get("markdown", True):
                md_path = out.with_suffix(".md")
                md_path.write_text(markdown + "\n", encoding="utf-8")
                artifacts.append(md_path)
            spec_path = tables.save_spec(spec, out.with_suffix(".spec.yaml"))
            artifacts.append(spec_path)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"表格渲染失败：{type(exc).__name__}: {exc}",
                content="请检查 columns/rows 列数是否一致；列分组 columns 必须是连续列索引。",
            )
        preview = "\n".join(latex.splitlines()[:14])
        content = (
            f"已生成三线表：{_summarize_artifacts(artifacts)}。"
            f"LaTeX 预览（前 14 行）：\n{preview}"
        )
        return ToolResult.success(
            content,
            data={
                "latex": latex,
                "markdown": markdown,
                "artifacts": [str(path.resolve()) for path in artifacts],
            },
            artifacts=_artifact_paths(artifacts),
        )


register_tool(FigureArchitectureTool())
register_tool(FigurePlotTool())
register_tool(FigureTableTool())


# ---------------------------------------------------------------------------
# CLI：sciforge fig ...
# ---------------------------------------------------------------------------


def _make_context(project_dir: Path | None = None) -> ToolContext:
    project = (project_dir or Path.cwd()).expanduser().resolve()
    settings = get_settings(project)
    return ToolContext(
        settings=settings,
        project_dir=project,
        data_dir=Path.home() / ".sciforge",
        session_dir=project / ".sciforge" / "sessions" / "cli",
    )


def _print_result(result: ToolResult, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"ok": result.ok, "content": result.content, "data": result.data}, ensure_ascii=False, indent=2, default=str))
        return
    typer.echo(result.content)
    if not result.ok and result.error:
        raise typer.Exit(code=1)


def add_cli(app: typer.Typer) -> None:
    """挂载 ``sciforge fig arch|plot|table|templates`` 子命令。"""
    fig_app = typer.Typer(
        name="fig",
        help="顶会级配图：模型架构图 / 数据图 / booktabs 三线表",
        no_args_is_help=True,
    )

    @fig_app.command("arch")
    def arch_cmd(
        spec: Path = typer.Option(..., "--spec", "-s", help="架构图 spec（YAML/JSON）"),
        out: Path = typer.Option(Path("figures/arch.svg"), "--out", "-o", help="输出路径（.svg/.pdf/.png）"),
        formats: str = typer.Option("", "--formats", help="输出格式，如 svg,pdf,png"),
        dpi: int = typer.Option(300, "--dpi", help="PNG 分辨率"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """渲染模型架构图（默认同时尝试 SVG/PDF/PNG）。"""
        ctx = _make_context()
        tool = FigureArchitectureTool()
        result = tool.run(
            ctx,
            spec=spec,
            out=out,
            formats=formats or None,
            dpi=dpi,
        )
        _print_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @fig_app.command("plot")
    def plot_cmd(
        spec: Path = typer.Option(..., "--spec", "-s", help="数据图 spec（YAML/JSON）"),
        out: Path = typer.Option(Path("figures/curve.pdf"), "--out", "-o", help="输出路径"),
        formats: str = typer.Option("", "--formats", help="输出格式，如 pdf,svg,png"),
        style: str = typer.Option("", "--style", help="neurips | cvpr | iclr | ieee"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """渲染论文数据图（line/bar/scatter/radar/heatmap）。"""
        ctx = _make_context()
        tool = FigurePlotTool()
        result = tool.run(
            ctx,
            spec=spec,
            out=out,
            formats=formats or None,
            style=style or None,
        )
        _print_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @fig_app.command("table")
    def table_cmd(
        spec: Path = typer.Option(..., "--spec", "-s", help="表格 spec（YAML/JSON）"),
        out: Path = typer.Option(Path("figures/tables/table.tex"), "--out", "-o", help="输出 .tex 路径"),
        markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="是否同时输出 .md"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """生成 booktabs 三线表（LaTeX + Markdown）。"""
        ctx = _make_context()
        tool = FigureTableTool()
        result = tool.run(ctx, spec=spec, out=out, markdown=markdown)
        _print_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @fig_app.command("templates")
    def templates_cmd(
        dest: Path = typer.Option(Path("figures/templates"), "--dest", "-d", help="模板输出目录"),
    ) -> None:
        """写出内置 spec 模板（transformer block / 多模态 pipeline / 训练循环 / 表格）。"""
        dest = dest.expanduser().resolve()
        paths = arch.write_builtin_templates(dest)
        paths += plots.write_builtin_templates(dest)
        paths += tables.write_builtin_templates(dest)
        for path in paths:
            typer.echo(f"已写出 {path}")
        typer.echo(f"共 {len(paths)} 个模板，见 {dest}")

    app.add_typer(fig_app, name="fig")


# 兼容 ARCHITECTURE §14.6 的 register(app) 命名
register = add_cli

__all__ = [
    "FigureArchitectureTool",
    "FigurePlotTool",
    "FigureTableTool",
    "add_cli",
    "register",
]
