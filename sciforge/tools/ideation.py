"""创新点相关工具：让 Agent 在对话中直接挖掘/评估想法。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool
from sciforge.ideation import IdeaCard, build_proposal, format_ideas, generate_ideas
from sciforge.llm.router import LLMRouter


def _router(ctx: ToolContext) -> LLMRouter:
    router = ctx.extra.get("router")
    if isinstance(router, LLMRouter):
        return router
    return LLMRouter(ctx.settings, force_mock=bool(ctx.extra.get("mock")))


def _read_context(ctx: ToolContext) -> str:
    """把项目内 notes/ 与文献库摘要拼成上下文。"""
    chunks: list[str] = []
    for path in sorted(ctx.project_dir.glob("notes/**/*.md"))[:8]:
        try:
            chunks.append(f"### {path.name}\n{path.read_text(encoding='utf-8', errors='replace')[:2500]}")
        except OSError:
            continue
    library = ctx.library_dir / "papers.jsonl"
    if library.exists():
        try:
            lines = library.read_text(encoding="utf-8").splitlines()[:20]
            chunks.append("### 文献库摘录\n" + "\n".join(line[:500] for line in lines))
        except OSError:
            pass
    return "\n\n".join(chunks)


class IdeaGenerateTool(BaseTool):
    name = "idea_generate"
    description = (
        "基于已有文献笔记/调研上下文，生成 3 个候选科研创新点（Idea Card：空白/洞察/方法/MVP/风险/评分），"
        "并写入项目 notes/ideas-*.md。适合从文献调研过渡到确定研究方向时使用。"
    )
    tags = ("ideation",)
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "研究主题"},
            "context": {"type": "string", "description": "可选：文献调研/精读结论；留空则读取项目 notes/"},
            "n": {"type": "integer", "description": "候选数量，默认 3", "default": 3},
        },
        "required": ["topic"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        topic = str(self.require(kwargs, "topic"))
        context = kwargs.get("context") or _read_context(ctx)
        cards = generate_ideas(
            ctx.settings,
            topic,
            context,
            n=int(kwargs.get("n") or 3),
            router=_router(ctx),
        )
        markdown = format_ideas(topic, cards)
        slug = "".join(ch if ch.isalnum() else "-" for ch in topic)[:40].strip("-") or "ideas"
        path = ctx.project_dir / "notes" / f"ideas-{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown + "\n", encoding="utf-8")
        return ToolResult.success(
            markdown,
            data={"ideas": [card.to_dict() for card in cards], "path": str(path)},
            artifacts=[str(path)],
        )


class IdeaProposalTool(BaseTool):
    name = "idea_proposal"
    description = "把选定的创新点（Idea Card JSON 或标题）扩写成完整研究 Proposal（背景/空白/洞察/方法/MVP/风险/里程碑）。"
    tags = ("ideation",)
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "idea": {
                "type": "object",
                "description": "Idea Card（title/gap/insight/approach/mvp/risks/novelty/feasibility/impact/related）",
            },
        },
        "required": ["topic", "idea"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        topic = str(self.require(kwargs, "topic"))
        raw = kwargs.get("idea") or {}
        if isinstance(raw, str):
            raw = {"title": raw}
        if not isinstance(raw, dict):
            return ToolResult.failure("idea 必须是对象")
        card = IdeaCard(
            title=str(raw.get("title") or "未命名想法"),
            gap=str(raw.get("gap") or ""),
            insight=str(raw.get("insight") or ""),
            approach=[str(x) for x in raw.get("approach") or []],
            mvp=str(raw.get("mvp") or ""),
            risks=[str(x) for x in raw.get("risks") or []],
            novelty=int(raw.get("novelty") or 3),
            feasibility=int(raw.get("feasibility") or 3),
            impact=int(raw.get("impact") or 3),
            related=[str(x) for x in raw.get("related") or []],
        )
        markdown = build_proposal(topic, card)
        path = ctx.project_dir / "notes" / "proposal.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown + "\n", encoding="utf-8")
        return ToolResult.success(markdown, data=card.to_dict(), artifacts=[str(path)])


register_tool(IdeaGenerateTool())
register_tool(IdeaProposalTool())


def add_cli(app) -> None:  # pragma: no cover - 由 cli.py 调用
    """挂载 `sciforge idea ...`。"""
    import typer
    from rich.console import Console

    idea_app = typer.Typer(help="创新点挖掘 / Proposal 生成", no_args_is_help=True)
    console = Console()

    @idea_app.command("generate")
    def generate(
        topic: str = typer.Argument(..., help="研究主题"),
        context_file: Path | None = typer.Option(None, "--context", help="文献调研/笔记文件"),
        n: int = typer.Option(3, "--n", help="候选数量"),
        mock: bool = typer.Option(False, "--mock", help="离线演示"),
        json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
    ) -> None:
        from sciforge.config import get_settings

        settings = get_settings()
        context = ""
        if context_file and context_file.exists():
            context = context_file.read_text(encoding="utf-8", errors="replace")
        cards = generate_ideas(settings, topic, context, n=n, mock=mock)
        if json_out:
            console.print_json(data=[card.to_dict() for card in cards])
            return
        console.print(format_ideas(topic, cards))

    @idea_app.command("proposal")
    def proposal(
        topic: str = typer.Argument(..., help="研究主题"),
        idea_json: Path = typer.Option(..., "--idea", help="Idea Card JSON 文件"),
    ) -> None:
        import json

        from sciforge.config import get_settings

        settings = get_settings()
        raw = json.loads(idea_json.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        cards = generate_ideas(settings, topic, mock=True)
        card = IdeaCard(**{k: v for k, v in raw.items() if k in IdeaCard.__dataclass_fields__}) if raw else cards[0]
        path = Path.cwd() / "notes" / "proposal.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_proposal(topic, card), encoding="utf-8")
        console.print(f"[green]已写入 {path}[/green]")

    app.add_typer(idea_app, name="idea")


__all__ = ["IdeaGenerateTool", "IdeaProposalTool", "add_cli"]
