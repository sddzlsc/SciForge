"""论文写作工具：``paper_init`` / ``paper_section`` / ``citation_add`` /
``paper_compile`` / ``paper_review``。

给 Agent 用的写作流水线入口：
1. ``paper_init`` 生成 LaTeX 工程骨架（cvpr/neurips/iclr/acl/generic）；
2. ``paper_section`` 写入/追加章节并自动在 main.tex 中 ``\\input``；
3. ``citation_add`` 合并 arXiv/DOI/手工 BibTeX 到 refs.bib（去重）；
4. ``paper_compile`` 检测 latexmk/xelatex 并编译（缺 LaTeX 时给安装提示，不崩）；
5. ``paper_review`` 审稿式自检（规则检查 + 可选 LLM），报告落盘 ``review.md``。

CLI：``add_cli(app)`` 挂 ``sciforge write init|draft|section|cite|compile|review|check``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import typer

from sciforge.config import get_settings
from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool
from sciforge.writing import citations
from sciforge.writing.paper import VENUES, PaperProject, create_paper_project
from sciforge.writing.review import self_review

_TAGS = ("writing",)
_KEY_FILES = ("main.tex", "refs.bib", "README.md")


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------


def _resolve_paper_dir(ctx: ToolContext, raw: Any) -> Path:
    default = ctx.project_dir / "paper"
    if not raw:
        return default
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = ctx.project_dir / path
    return path


def _split_authors(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [part.strip() for part in re.split(r"[,;，；、]", str(raw)) if part.strip()]


def _project_summary(project: PaperProject) -> dict[str, Any]:
    stats = project.stats()
    return {
        "dir": str(project.dest),
        "venue": project.venue,
        "title": project.meta.get("title", ""),
        "sections": project.list_sections(),
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Tool: 初始化论文工程
# ---------------------------------------------------------------------------


class PaperInitTool(BaseTool):
    name = "paper_init"
    description = (
        "生成可编译的 LaTeX 论文工程骨架（venue: cvpr/neurips/iclr/acl/generic）。"
        "generic 模板不依赖官方 sty，可直接编译；会议模板在 preamble 注释里说明所需 sty。"
        "产物：main.tex / sections/*.tex / refs.bib / figures/ / tables/ / README.md。"
        "什么时候用：开始写论文、demo 创建 paper/ 目录时。"
    )
    tags = _TAGS
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "dest": {"type": "string", "description": "论文工程目录（默认 <project>/paper）"},
            "venue": {"type": "string", "description": "cvpr | neurips | iclr | acl | generic"},
            "title": {"type": "string", "description": "论文标题"},
            "authors": {
                "type": ["array", "string"],
                "description": "作者列表（数组或逗号分隔字符串）",
                "items": {"type": "string"},
            },
            "overwrite": {"type": "boolean", "description": "已存在时是否覆盖（默认 false）"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        paper_dir = _resolve_paper_dir(ctx, kwargs.get("dest"))
        venue = str(kwargs.get("venue") or ctx.settings.writing.venue or "generic").strip().lower()
        title = str(kwargs.get("title") or ctx.settings.project_name or "Untitled Paper")
        authors = _split_authors(kwargs.get("authors"))
        try:
            if paper_dir.exists() and (paper_dir / "main.tex").exists():
                if not kwargs.get("overwrite"):
                    project = PaperProject(paper_dir)
                    return ToolResult.success(
                        f"论文工程已存在，未覆盖：{paper_dir}（加 overwrite=true 可重建）。",
                        data=_project_summary(project),
                        artifacts=[str(paper_dir / name) for name in _KEY_FILES if (paper_dir / name).exists()],
                    )
                for name in _KEY_FILES:
                    target = paper_dir / name
                    if target.exists():
                        target.unlink()
            create_paper_project(paper_dir, venue=venue, title=title, authors=authors)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"论文工程创建失败：{type(exc).__name__}: {exc}",
                content=(
                    f"请检查目录是否可写、venue 是否为 {', '.join(VENUES)}。"
                    "如果目标目录已有 main.tex，换一个目录或传 overwrite=true。"
                ),
            )
        project = PaperProject(paper_dir)
        artifacts = [paper_dir / name for name in _KEY_FILES if (paper_dir / name).exists()]
        artifacts += sorted((paper_dir / "sections").glob("*.tex"))
        content = (
            f"已生成 {VENUES.get(venue, {}).get('label', venue)} 论文工程：{paper_dir}\n"
            f"标题：{title}；作者：{', '.join(project.meta.get('authors') or []) or '（未填）'}\n"
            f"章节：{', '.join(project.list_sections())}\n"
            f"下一步：`sciforge write section --name 01_introduction --content-file intro.tex`，"
            f"然后 `sciforge write compile`。\n"
            f"注意：文中 TODO 需替换为真实内容（{ctx.settings.writing.venue} 模板会自动写 preamble）。"
        )
        return ToolResult.success(
            content,
            data=_project_summary(project),
            artifacts=[str(path.resolve()) for path in artifacts],
        )


# ---------------------------------------------------------------------------
# Tool: 写章节
# ---------------------------------------------------------------------------


class PaperSectionTool(BaseTool):
    name = "paper_section"
    description = (
        "写入/追加论文的一个章节：自动保存到 sections/<name>.tex 并在 main.tex 中插入 \\input。"
        "content 可直接给 LaTeX 文本，也可用 content_file 指定文件；append=true 时追加到已有章节。"
    )
    tags = _TAGS
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "章节名，如 01_introduction / experiments"},
            "content": {"type": "string", "description": "章节 LaTeX 内容"},
            "content_file": {"type": "string", "description": "从文件读取章节内容（相对项目目录）"},
            "paper_dir": {"type": "string", "description": "论文工程目录（默认 <project>/paper）"},
            "append": {"type": "boolean", "description": "追加而不是覆盖（默认 false）"},
        },
        "required": ["name"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        name = str(self.require(kwargs, "name")).strip()
        paper_dir = _resolve_paper_dir(ctx, kwargs.get("paper_dir"))
        project = PaperProject(paper_dir)
        if not project.exists:
            return ToolResult.failure(
                f"不是论文工程（缺少 main.tex）：{paper_dir}",
                content="请先运行 `sciforge write init --venue generic` 创建论文工程。",
            )
        content = kwargs.get("content")
        if kwargs.get("content_file"):
            path = Path(str(kwargs["content_file"])).expanduser()
            if not path.is_absolute():
                path = ctx.project_dir / path
            if not path.exists():
                return ToolResult.failure(f"内容文件不存在：{path}")
            content = path.read_text(encoding="utf-8", errors="replace")
        if content is None:
            return ToolResult.failure("缺少章节内容：请提供 content 或 content_file")
        try:
            if kwargs.get("append"):
                path = project.append_to_section(name, str(content))
            else:
                path = project.add_section(name, str(content))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"章节写入失败：{type(exc).__name__}: {exc}")
        words = len(str(content).split())
        return ToolResult.success(
            f"已写入章节 sections/{path.stem}.tex（约 {words} 词），并确保 main.tex 已 \\input。",
            data={"path": str(path), "section": path.stem, "words": words},
            artifacts=[str(path.resolve()), str(project.main_tex.resolve())],
        )


# ---------------------------------------------------------------------------
# Tool: 引用管理
# ---------------------------------------------------------------------------


class CitationAddTool(BaseTool):
    name = "citation_add"
    description = (
        "添加参考文献：支持手工 BibTeX 文本、arXiv id 列表、DOI 列表；自动合并进 refs.bib 并按 cite key 去重。"
        "会返回新增/合并后的条目数。网络失败时给出可操作建议（也可直接传 entries 文本）。"
        "什么时候用：Related Work 需要引用；把文献库里的论文写进论文参考文献时。"
    )
    tags = _TAGS
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "entries": {"type": "string", "description": "BibTeX 文本（可直接粘贴多条）"},
            "arxiv": {
                "type": ["string", "array"],
                "description": "arXiv id 列表，如 ['2401.00001', '2402.00002']",
                "items": {"type": "string"},
            },
            "doi": {
                "type": ["string", "array"],
                "description": "DOI 列表（Crossref 元数据）",
                "items": {"type": "string"},
            },
            "paper_dir": {"type": "string", "description": "论文工程目录（默认 <project>/paper）"},
            "bib": {"type": "string", "description": "直接指定 refs.bib 路径"},
            "dry_run": {"type": "boolean", "description": "只返回 BibTeX 文本，不写入文件"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        chunks: list[str] = []
        sources: list[str] = []
        try:
            if kwargs.get("entries"):
                chunks.append(str(kwargs["entries"]))
                sources.append("手工 BibTeX")
            if kwargs.get("arxiv"):
                text = citations.collect_from_arxiv(kwargs["arxiv"])
                chunks.append(text)
                sources.append("arXiv")
            if kwargs.get("doi"):
                text = citations.collect_from_doi(kwargs["doi"])
                chunks.append(text)
                sources.append("Crossref DOI")
        except citations.CitationError as exc:
            return ToolResult.failure(str(exc), content=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"参考文献获取失败：{type(exc).__name__}: {exc}")
        if not chunks:
            return ToolResult.failure(
                "没有可添加的引用：请提供 entries（BibTeX 文本）或 arxiv/doi 列表"
            )
        merged = citations.merge_bibtex(*chunks)
        keys = citations.bib_keys(merged)
        if kwargs.get("dry_run"):
            return ToolResult.success(
                f"解析到 {len(keys)} 条 BibTeX（dry_run，未写文件）：{', '.join(keys)}",
                data={"bibtex": merged, "keys": keys},
            )
        if kwargs.get("bib"):
            bib_path = Path(str(kwargs["bib"])).expanduser()
            if not bib_path.is_absolute():
                bib_path = ctx.project_dir / bib_path
        else:
            paper_dir = _resolve_paper_dir(ctx, kwargs.get("paper_dir"))
            if (paper_dir / "main.tex").exists():
                bib_path = paper_dir / "refs.bib"
            else:
                bib_path = ctx.project_dir / "refs.bib"
        try:
            written = citations.add_bibtex(merged, bib_path)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"写入 refs.bib 失败：{type(exc).__name__}: {exc}")
        all_keys = citations.bib_keys(written.read_text(encoding="utf-8"))
        return ToolResult.success(
            f"已从 {'/'.join(sources)} 添加 {len(keys)} 条引用到 {written}"
            f"（当前共 {len(all_keys)} 条）：{', '.join(keys)}",
            data={
                "bib_path": str(written),
                "added_keys": keys,
                "total_keys": len(all_keys),
                "bibtex": merged,
            },
            artifacts=[str(written.resolve())],
        )


# ---------------------------------------------------------------------------
# Tool: 编译
# ---------------------------------------------------------------------------


class PaperCompileTool(BaseTool):
    name = "paper_compile"
    description = (
        "编译 LaTeX 论文：自动检测 latexmk/xelatex/pdflatex。缺 LaTeX 时返回明确安装提示（不抛异常、不崩溃）。"
        "成功时返回 main.pdf 路径与日志摘要，失败时给出缺失 sty / 未定义引用等可操作诊断。"
    )
    tags = _TAGS
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "paper_dir": {"type": "string", "description": "论文工程目录（默认 <project>/paper）"},
            "timeout": {"type": "number", "description": "编译超时秒数（默认 240）"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        project = PaperProject(_resolve_paper_dir(ctx, kwargs.get("paper_dir")))
        if not project.exists:
            return ToolResult.failure(
                f"不是论文工程（缺少 main.tex）：{project.dest}",
                content="请先运行 `sciforge write init --venue generic`。",
            )
        try:
            pdf = project.compile(timeout=float(kwargs.get("timeout") or 240))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"编译流程异常：{type(exc).__name__}: {exc}")
        info = dict(project.last_compile)
        if pdf is None:
            # 环境缺 LaTeX 属于“可解释的降级”，ok=True 但 compiled=False
            return ToolResult.success(
                info.get("message", "编译失败"),
                data={"compiled": False, **{key: info.get(key) for key in ("engine", "message")}},
            )
        return ToolResult.success(
            f"编译成功：{pdf}",
            data={
                "compiled": True,
                "pdf": str(pdf),
                "engine": info.get("engine"),
                "message": info.get("message"),
            },
            artifacts=[str(pdf.resolve())],
        )


# ---------------------------------------------------------------------------
# Tool: 审稿式自检
# ---------------------------------------------------------------------------


class PaperReviewTool(BaseTool):
    name = "paper_review"
    description = (
        "审稿式自检：读论文工程 + 可选 results/metrics.json，输出 Major/Minor 问题清单与下一步建议。"
        "默认只做规则检查（不联网、不需要 API key）；use_llm=true 时若有 LLM 配置则追加 reviewer 意见。"
        "报告写入 <paper_dir>/review.md。"
    )
    tags = _TAGS
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "paper_dir": {"type": "string", "description": "论文工程目录（默认 <project>/paper）"},
            "results_dir": {"type": "string", "description": "实验结果目录（默认 <project>/results）"},
            "use_llm": {"type": "boolean", "description": "是否尝试 LLM 审稿（默认 false）"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        paper_dir = _resolve_paper_dir(ctx, kwargs.get("paper_dir"))
        project = PaperProject(paper_dir)
        if not project.exists:
            return ToolResult.failure(
                f"不是论文工程（缺少 main.tex）：{paper_dir}",
                content="请先运行 `sciforge write init`。",
            )
        results_dir = kwargs.get("results_dir")
        results_path = None
        if results_dir:
            results_path = Path(str(results_dir)).expanduser()
            if not results_path.is_absolute():
                results_path = ctx.project_dir / results_path
        elif (ctx.project_dir / "results").exists():
            results_path = ctx.project_dir / "results"
        try:
            report = self_review(
                paper_dir,
                results_dir=results_path,
                use_llm=bool(kwargs.get("use_llm")),
            )
            report_path = paper_dir / "review.md"
            report_path.write_text(report, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"审稿自检失败：{type(exc).__name__}: {exc}")
        n_major = report.count("**") and len(re.findall(r"^\d+\. \*\*\[", report, re.M))
        n_minor = len(re.findall(r"^\d+\. \[", report, re.M))
        preview = report[:2000]
        return ToolResult.success(
            f"审稿式自检完成：Major 约 {n_major} 条、Minor 约 {n_minor} 条。报告：{report_path}\n\n{preview}",
            data={
                "report": report,
                "report_path": str(report_path),
                "major": n_major,
                "minor": n_minor,
            },
            artifacts=[str(report_path.resolve())],
        )


register_tool(PaperInitTool())
register_tool(PaperSectionTool())
register_tool(CitationAddTool())
register_tool(PaperCompileTool())
register_tool(PaperReviewTool())


# ---------------------------------------------------------------------------
# CLI：sciforge write ...
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


def _print_tool_result(result: ToolResult, as_json: bool) -> None:
    if as_json:
        typer.echo(
            json.dumps(
                {"ok": result.ok, "content": result.content, "data": result.data, "error": result.error},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return
    typer.echo(result.content)


def add_cli(app: typer.Typer) -> None:
    """挂载 ``sciforge write init|draft|section|cite|compile|review|check`` 子命令。"""
    write_app = typer.Typer(
        name="write",
        help="论文写作：LaTeX 工程 / 章节 / 参考文献 / 编译 / 审稿式自检",
        no_args_is_help=True,
    )

    @write_app.command("init")
    def init_cmd(
        dest: Path = typer.Option(Path("paper"), "--dest", "-d", help="论文工程目录"),
        venue: str = typer.Option("generic", "--venue", help="cvpr | neurips | iclr | acl | generic"),
        title: str = typer.Option("", "--title", "-t", help="论文标题"),
        authors: str = typer.Option("", "--authors", help="作者，逗号分隔"),
        overwrite: bool = typer.Option(False, "--overwrite", help="已存在时覆盖"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """生成 LaTeX 论文工程骨架。"""
        ctx = _make_context()
        result = PaperInitTool().run(
            ctx,
            dest=dest,
            venue=venue,
            title=title or None,
            authors=authors,
            overwrite=overwrite,
        )
        _print_tool_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @write_app.command("draft")
    def draft_cmd(
        venue: str = typer.Option("generic", "--venue", help="cvpr | neurips | iclr | acl | generic"),
        title: str = typer.Option("", "--title", "-t", help="论文标题"),
        authors: str = typer.Option("", "--authors", help="作者，逗号分隔"),
        dest: Path = typer.Option(Path("paper"), "--dest", "-d", help="论文工程目录"),
    ) -> None:
        """`write draft` 是 `write init` 的别名（ARCHITECTURE §9 的入口）。"""
        ctx = _make_context()
        result = PaperInitTool().run(
            ctx,
            dest=dest,
            venue=venue,
            title=title or None,
            authors=authors,
        )
        _print_tool_result(result, False)
        if not result.ok:
            raise typer.Exit(code=1)

    @write_app.command("section")
    def section_cmd(
        name: str = typer.Option(..., "--name", "-n", help="章节名，如 04_experiments"),
        content: str = typer.Option("", "--content", help="章节 LaTeX 内容"),
        content_file: Path | None = typer.Option(None, "--content-file", help="从文件读取内容"),
        paper_dir: Path = typer.Option(Path("paper"), "--dir", help="论文工程目录"),
        append: bool = typer.Option(False, "--append", help="追加而非覆盖"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """写入/追加章节并在 main.tex 中挂载。"""
        ctx = _make_context()
        if not content and content_file is None:
            typer.echo("需要 --content 或 --content-file", err=True)
            raise typer.Exit(code=2)
        result = PaperSectionTool().run(
            ctx,
            name=name,
            content=content or None,
            content_file=content_file,
            paper_dir=paper_dir,
            append=append,
        )
        _print_tool_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @write_app.command("cite")
    def cite_cmd(
        arxiv: str = typer.Option("", "--arxiv", help="arXiv id，逗号分隔"),
        doi: str = typer.Option("", "--doi", help="DOI，逗号分隔"),
        entries: str = typer.Option("", "--entries", help="BibTeX 文本"),
        entries_file: Path | None = typer.Option(None, "--entries-file", help="BibTeX 文件"),
        paper_dir: Path = typer.Option(Path("paper"), "--dir", help="论文工程目录"),
        bib: Path | None = typer.Option(None, "--bib", help="直接指定 refs.bib"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """添加/合并参考文献（arXiv / DOI / BibTeX，自动去重）。"""
        ctx = _make_context()
        if entries_file is not None:
            if not entries_file.exists():
                typer.echo(f"BibTeX 文件不存在：{entries_file}", err=True)
                raise typer.Exit(code=1)
            entries = entries_file.read_text(encoding="utf-8")
        result = CitationAddTool().run(
            ctx,
            entries=entries or None,
            arxiv=arxiv or None,
            doi=doi or None,
            paper_dir=paper_dir,
            bib=bib,
        )
        _print_tool_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @write_app.command("compile")
    def compile_cmd(
        paper_dir: Path = typer.Option(Path("paper"), "--dir", help="论文工程目录"),
        timeout: float = typer.Option(240.0, "--timeout", help="编译超时秒数"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """编译论文（自动检测 latexmk/xelatex；缺失时给安装提示）。"""
        ctx = _make_context()
        result = PaperCompileTool().run(ctx, paper_dir=paper_dir, timeout=timeout)
        _print_tool_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @write_app.command("review")
    def review_cmd(
        paper_dir: Path = typer.Option(Path("paper"), "--dir", help="论文工程目录"),
        results_dir: Path | None = typer.Option(None, "--results", help="实验结果目录"),
        use_llm: bool = typer.Option(False, "--llm/--no-llm", help="是否尝试 LLM 审稿"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """审稿式自检（规则 + 可选 LLM），报告写入 review.md。"""
        ctx = _make_context()
        result = PaperReviewTool().run(
            ctx,
            paper_dir=paper_dir,
            results_dir=results_dir,
            use_llm=use_llm,
        )
        _print_tool_result(result, json_out)
        if not result.ok:
            raise typer.Exit(code=1)

    @write_app.command("check")
    def check_cmd(
        paper_dir: Path = typer.Option(Path("paper"), "--dir", help="论文工程目录"),
        json_out: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """静态检查 TODO/未定义引用/缺失图表/摘要长度等。"""
        project = PaperProject(paper_dir)
        if not project.exists:
            typer.echo(f"不是论文工程（缺少 main.tex）：{project.dest}", err=True)
            raise typer.Exit(code=1)
        issues = project.check()
        if json_out:
            typer.echo(json.dumps(issues, ensure_ascii=False, indent=2, default=str))
        else:
            for issue in issues:
                location = f"{issue.get('file', '')}:{issue.get('line', '')}" if issue.get("file") else ""
                typer.echo(f"[{issue['severity']:7s}] {issue['kind']:20s} {location} {issue['message']}")
            typer.echo(f"\n共 {len(issues)} 个问题（check-report.json 已写入论文工程目录）")

    app.add_typer(write_app, name="write")


# 兼容 ARCHITECTURE §14.6 的 register(app) 命名
register = add_cli

__all__ = [
    "PaperInitTool",
    "PaperSectionTool",
    "CitationAddTool",
    "PaperCompileTool",
    "PaperReviewTool",
    "add_cli",
    "register",
]
