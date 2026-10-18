"""Skill 工具与 CLI：让 Agent 按需加载技能，让用户安装/体检技能。

工具（import 本模块即自注册）::

    skill_list     列出 bundled/installed/external/user 技能
    skill_load     加载某个 skill 的完整 SKILL.md（可附带 references/scripts 清单）
    skill_search   按名字/描述/tag/正文检索
    skill_install  安装本地目录 / GitHub 源 / 外部生态目录条目
    skill_lint     按 skill-metric 规则做 0-30 分体检

CLI（``sciforge skills ...``，由 cli.py 调 :func:`add_cli` / :func:`register`）::

    sciforge skills list [--source S] [--tag T] [--json]
    sciforge skills search "科研配图" [--limit N] [--json]
    sciforge skills show literature-survey [--content-only] [--json]
    sciforge skills install <source|目录条目名> [--name N] [--target T] [--catalog]
    sciforge skills lint [NAME] [--all] [--json]
    sciforge skills install-dsh [NAME] [--all]
    sciforge skills catalog [QUERY] [--json]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool
from sciforge.skills_manager import (
    SkillError,
    SkillInstallError,
    SkillManager,
    SkillMeta,
    SkillNotFoundError,
)

CONTENT_LIMIT = 12000


def _manager(ctx: ToolContext | None = None) -> SkillManager:
    """优先复用会话级 manager（ctx.extra），否则按 settings 新建。"""
    if ctx is not None:
        cached = ctx.extra.get("skill_manager")
        if isinstance(cached, SkillManager):
            return cached
        manager = SkillManager(settings=ctx.settings)
        ctx.extra["skill_manager"] = manager
        return manager
    return SkillManager()


def _summary(meta: SkillMeta) -> dict[str, Any]:
    return meta.to_dict()


def _table(skills: list[SkillMeta]) -> str:
    if not skills:
        return "（没有找到 skill）"
    name_width = max(len("name"), *(len(skill.name) for skill in skills))
    source_width = max(len("source"), *(len(skill.source) for skill in skills))
    lines = [f"{'name'.ljust(name_width)}  {'source'.ljust(source_width)}  version   tags / description"]
    for skill in skills:
        tags = ",".join(skill.tags)
        desc = skill.description.replace("\n", " ")
        if len(desc) > 72:
            desc = desc[:71] + "…"
        lines.append(
            f"{skill.name.ljust(name_width)}  {skill.source.ljust(source_width)}  "
            f"{(skill.version or '-').ljust(7)}  {tags or '-'} · {desc}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent 工具
# ---------------------------------------------------------------------------


class SkillListTool(BaseTool):
    name = "skill_list"
    description = (
        "列出当前可用的科研 Skill（内置 bundled、已安装 installed、外部目录 external、项目 user）。"
        "不确定该用哪个技能时先调用本工具；可用 tag 过滤（literature/reading/ideation/experiment/"
        "remote/writing/figure/review/venue/reproducibility/meta）。"
    )
    tags = ("skill",)
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "只列出来源：bundled|installed|external|user"},
            "tag": {"type": "string", "description": "按 tag 过滤，如 literature、figure"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        manager = _manager(ctx)
        skills = manager.list(source=kwargs.get("source"), tag=kwargs.get("tag"))
        data = {"count": len(skills), "skills": [_summary(skill) for skill in skills]}
        return ToolResult.success(_table(skills), data=data)


class SkillLoadTool(BaseTool):
    name = "skill_load"
    description = (
        "加载一个科研 Skill 的完整使用说明（SKILL.md 正文 + frontmatter），并可选列出其 references/scripts。"
        "在开始文献调研、实验设计、远程训练、配图、写作等任务前调用，按 skill 工作流执行。"
    )
    tags = ("skill",)
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "skill 名称，如 literature-survey"},
            "include_resources": {"type": "boolean", "description": "是否附带 references/scripts 清单，默认 true"},
        },
        "required": ["name"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        manager = _manager(ctx)
        try:
            name = self.require(kwargs, "name")
            content = manager.load(name)
            meta = manager.get(name)
        except SkillError as exc:
            return ToolResult.failure(str(exc))
        assert meta is not None  # load 成功则 get 必然存在
        resources: list[str] = []
        if kwargs.get("include_resources", True):
            try:
                resources = manager.resources(meta.name)
            except SkillError:
                resources = []
        text = content
        if len(text) > CONTENT_LIMIT:
            text = text[:CONTENT_LIMIT] + f"\n…（已截断，完整内容见 {meta.path}）"
        if resources:
            text += "\n\n[可用扩展资源（需要细节时用 skill_load 后自行读取）]\n" + "\n".join(
                f"- {meta.skill_dir / item}" for item in resources
            )
        return ToolResult.success(
            text,
            data={"name": meta.name, "path": str(meta.path), "resources": resources, "source": meta.source},
            artifacts=[],
        )


class SkillSearchTool(BaseTool):
    name = "skill_search"
    description = (
        "按自然语言描述检索可用科研 Skill（匹配名字/描述/tags/正文）。"
        "例如“如何排查 loss NaN”“画模型架构图”“写审稿回复”。"
    )
    tags = ("skill",)
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词，中文或英文"},
            "limit": {"type": "integer", "description": "最多返回条数，默认 10"},
        },
        "required": ["query"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        manager = _manager(ctx)
        try:
            query = str(self.require(kwargs, "query"))
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        limit = int(kwargs.get("limit") or 10)
        skills = manager.search(query, limit=limit)
        if not skills:
            return ToolResult.success(
                f"没有匹配 {query!r} 的 skill；可先 `skill_list` 查看全部技能。",
                data={"query": query, "count": 0, "skills": []},
            )
        lines = [f"检索 {query!r} 命中 {len(skills)} 个 skill："]
        for skill in skills:
            lines.append(f"- {skill.name} [{','.join(skill.tags)}]：{skill.description}")
        return ToolResult.success(
            "\n".join(lines),
            data={"query": query, "count": len(skills), "skills": [_summary(skill) for skill in skills]},
        )


class SkillInstallTool(BaseTool):
    name = "skill_install"
    description = (
        "安装科研 Skill：支持本地 skill 目录、GitHub 仓库/子目录 URL、owner/repo 简写、单个 SKILL.md URL，"
        "以及 catalog 中的外部生态条目。target 可为 dsh（默认推荐）、claude、codex 或具体路径。"
    )
    tags = ("skill",)
    requires = ("git",)
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "本地目录 / GitHub URL / owner/repo / 目录条目名"},
            "target": {"type": "string", "description": "安装目标：dsh|claude|codex|project|路径，默认装到 ~/.sciforge/skills"},
            "name": {"type": "string", "description": "只安装源中的指定 skill / 重命名目标目录"},
            "from_catalog": {"type": "boolean", "description": "把 source 当作 catalog 条目名解释"},
        },
        "required": ["source"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        manager = _manager(ctx)
        source = str(self.require(kwargs, "source"))
        target = kwargs.get("target")
        name = kwargs.get("name")
        try:
            if kwargs.get("from_catalog"):
                paths = manager.install_from_catalog(source, target=target, skill=name)
            else:
                paths = manager.install(source, target=target, name=name)
        except SkillError as exc:
            return ToolResult.failure(str(exc))
        lines = [f"已安装 {len(paths)} 个 skill："]
        lines += [f"- {path}" for path in paths]
        lines += manager.last_install_messages
        if target:
            lines.append(manager.target_hint(str(target)))
        return ToolResult.success(
            "\n".join(lines),
            data={"installed": [str(path) for path in paths], "target": str(target or "installed")},
            artifacts=[str(path) for path in paths],
        )


class SkillLintTool(BaseTool):
    name = "skill_lint"
    description = "按 skill-metric 规则给 skill 做 0-30 分静态体检（格式/完整性/写作结构），返回问题清单。"
    tags = ("skill",)
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "skill 名称"}},
        "required": ["name"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        manager = _manager(ctx)
        try:
            report = manager.lint(str(self.require(kwargs, "name")))
        except SkillError as exc:
            return ToolResult.failure(str(exc))
        lines = [f"{report['name']}: {report['score']}/{report['max_score']} 分，{'通过' if report['ok'] else '未通过'}"]
        lines += [f"- {issue}" for issue in report["issues"]] or ["- 无问题，很棒。"]
        return ToolResult.success("\n".join(lines), data=report)


for _tool in (SkillListTool(), SkillLoadTool(), SkillSearchTool(), SkillInstallTool(), SkillLintTool()):
    register_tool(_tool)
    del _tool


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

skills_app = typer.Typer(
    name="skills",
    help="科研 Skill 体系：列出/搜索/查看/安装/lint/装进 DSH。",
    no_args_is_help=True,
    add_completion=False,
)


def _manager_for_cli() -> SkillManager:
    return SkillManager()


def _echo_json(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _find_by_name(manager: SkillManager, name: str) -> SkillMeta:
    meta = manager.get(name)
    if meta is None:
        _fail(f"未找到 skill: {name}；用 `sciforge skills list` 查看可用技能。")
        raise AssertionError("unreachable")
    return meta


@skills_app.command("list")
def cli_list(
    source: str | None = typer.Option(None, "--source", "-s", help="bundled|installed|external|user"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="按 tag 过滤"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """列出所有可用 skill。"""
    manager = _manager_for_cli()
    skills = manager.list(source=source, tag=tag)
    if json_output:
        _echo_json({"count": len(skills), "skills": [_summary(skill) for skill in skills]})
        return
    typer.echo(_table(skills))
    if skills:
        typer.echo(f"\n共 {len(skills)} 个 skill。加载正文：sciforge skills show <name>；装进 DSH：sciforge skills install-dsh <name>")


@skills_app.command("search")
def cli_search(
    query: str = typer.Argument(..., help="检索词，中文或英文"),
    limit: int = typer.Option(10, "--limit", "-n", help="最多返回条数"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """按名字/描述/tags/正文检索 skill。"""
    manager = _manager_for_cli()
    skills = manager.search(query, limit=limit)
    if json_output:
        _echo_json({"query": query, "count": len(skills), "skills": [_summary(skill) for skill in skills]})
        return
    if not skills:
        typer.echo(f"没有匹配 {query!r} 的 skill。")
        return
    for skill in skills:
        typer.echo(f"- {skill.name} [{','.join(skill.tags)}]：{skill.description}")


@skills_app.command("show")
def cli_show(
    name: str = typer.Argument(..., help="skill 名称"),
    content_only: bool = typer.Option(False, "--content-only", help="只打印正文（去掉 frontmatter）"),
    resources: bool = typer.Option(False, "--resources", help="同时列出 references/scripts 清单"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """查看某个 skill 的完整内容与扩展资源。"""
    manager = _manager_for_cli()
    meta = _find_by_name(manager, name)
    raw = manager.load(meta.name)
    resource_list = manager.resources(meta.name) if resources else []
    if content_only:
        from sciforge.skills_manager import split_frontmatter

        _, raw, _ = split_frontmatter(raw)
    if json_output:
        _echo_json({**_summary(meta), "content": raw, "resources": resource_list})
        return
    typer.echo(raw)
    if resource_list:
        typer.echo("\n扩展资源：")
        for item in resource_list:
            typer.echo(f"- {meta.skill_dir / item}")


@skills_app.command("install")
def cli_install(
    source: str = typer.Argument(..., help="本地目录 / GitHub URL / owner/repo / SKILL.md URL / catalog 条目名"),
    name: str | None = typer.Option(None, "--name", "-n", help="只装源中的指定 skill（或重命名目标目录）"),
    target: str | None = typer.Option(None, "--target", "-t", help="dsh|claude|codex|project|具体路径；默认 ~/.sciforge/skills"),
    from_catalog: bool = typer.Option(False, "--catalog", help="把 source 当作外部生态目录条目名"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """安装 skill（本地目录 / GitHub / owner-repo / 单个 SKILL.md / 生态目录条目）。"""
    manager = _manager_for_cli()
    try:
        if from_catalog:
            paths = manager.install_from_catalog(source, target=target, skill=name)
        else:
            paths = manager.install(source, target=target, name=name)
    except SkillError as exc:
        _fail(str(exc))
        return
    if json_output:
        _echo_json({"installed": [str(path) for path in paths], "messages": manager.last_install_messages})
        return
    for path in paths:
        typer.echo(f"✓ {path}")
    for message in manager.last_install_messages:
        typer.echo(f"  {message}")
    if target:
        typer.echo(manager.target_hint(target))


@skills_app.command("lint")
def cli_lint(
    name: str | None = typer.Argument(None, help="skill 名称；--all 时忽略"),
    all_skills: bool = typer.Option(False, "--all", "-a", help="体检全部已发现 skill"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """按 skill-metric 规则给 skill 做 0-30 分体检。"""
    manager = _manager_for_cli()
    try:
        if all_skills:
            reports = [manager.lint(skill.name) for skill in manager.list()]
        elif name:
            reports = [manager.lint(name)]
        else:
            _fail("请给出 skill 名称，或使用 --all。")
            return
    except SkillError as exc:
        _fail(str(exc))
        return
    if json_output:
        _echo_json(reports)
        return
    for report in reports:
        status = "✓ 通过" if report["ok"] else "✗ 未通过"
        typer.echo(f"{status}  {report['name']}: {report['score']}/{report['max_score']} 分（正文 {report['body_chars']} 字符）")
        for issue in report["issues"]:
            typer.echo(f"   - {issue}")
    if len(reports) > 1:
        passed = sum(1 for report in reports if report["ok"])
        typer.echo(f"\n合计 {passed}/{len(reports)} 个 skill 通过（>=24 分且无 critical 问题）。")


@skills_app.command("install-dsh")
def cli_install_dsh(
    name: str | None = typer.Argument(None, help="要装进 DSH 的 skill 名称"),
    all_skills: bool = typer.Option(False, "--all", "-a", help="安装全部 skill 到 DSH"),
    source: str | None = typer.Option(None, "--source", "-s", help="配合 --all，只装某个来源"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """把 skill 复制到 ~/.dsh/skills/（重启或新开会话后 DSH 可用）。"""
    manager = _manager_for_cli()
    try:
        if all_skills:
            paths = manager.install_all_to("dsh", source=source)
        elif name:
            paths = [manager.install_to(name, "dsh")]
        else:
            _fail("请给出 skill 名称，或使用 --all。")
            return
    except SkillError as exc:
        _fail(str(exc))
        return
    if json_output:
        _echo_json(
            {
                "target": str(manager.target_dir("dsh")),
                "installed": [str(path) for path in paths],
                "messages": manager.last_install_messages,
            }
        )
        return
    for path in paths:
        typer.echo(f"✓ {path}")
    for message in manager.last_install_messages:
        typer.echo(f"  {message}")
    typer.secho(manager.target_hint("dsh"), fg=typer.colors.GREEN)


@skills_app.command("catalog")
def cli_catalog(
    query: str | None = typer.Argument(None, help="按名字/描述/tags 过滤"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """查看可安装的外部生态 skill 目录（k-dense / Awesome-Scientific-Skills / Rimagination / Anthropic / HF ...）。"""
    manager = _manager_for_cli()
    entries = manager.catalog()
    needle = (query or "").lower()
    if needle:
        entries = [
            entry
            for entry in entries
            if needle in json.dumps(entry, ensure_ascii=False).lower()
        ]
    if json_output:
        _echo_json({"count": len(entries), "skills": entries})
        return
    if not entries:
        typer.echo("外部生态目录为空（sciforge/catalog/skills.json 缺失或被过滤）。")
        return
    for entry in entries:
        verified = "verified" if entry.get("verified") else "community"
        typer.echo(f"- {entry.get('name')}  [{verified}]  {entry.get('url')}")
        typer.echo(f"    {entry.get('description', '')}")
        if entry.get("how_to_install"):
            typer.echo(f"    安装: {entry['how_to_install']}")
    typer.echo("\n安装示例：sciforge skills install <条目名> --catalog [--target dsh]")


def add_cli(app: typer.Typer) -> None:
    """供 cli.py 挂载：``add_cli(app)``。"""
    app.add_typer(skills_app, name="skills")


def register(app: typer.Typer) -> None:
    """与 :func:`add_cli` 等价（ARCHITECTURE §14.6 的 register 约定）。"""
    add_cli(app)


__all__ = [
    "skills_app",
    "add_cli",
    "register",
    "SkillListTool",
    "SkillLoadTool",
    "SkillSearchTool",
    "SkillInstallTool",
    "SkillLintTool",
]


# 明确不注册的“死代码”防护：Path 仅用于类型标注
_ = Path
