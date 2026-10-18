"""SciForge 命令行入口。

命令组织：
- 核心：init / doctor / chat / pipeline / demo / serve / tools / config / llm / dsh
- 模块子命令由各工具模块的 ``add_cli(app)`` 自注册（lit/read/ssh/gpu/exp/fig/write/skills/idea）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

import sciforge
from sciforge.config import (
    DEFAULT_CONFIG_TOML,
    ConfigError,
    Settings,
    data_home,
    ensure_dirs,
    get_settings,
    global_config_path,
    project_config_path,
    save_project_config,
)
from sciforge.core.events import Event
from sciforge.core.registry import list_tools, run_tool

app = typer.Typer(
    name="sciforge",
    help="SciForge — 面向深度学习科研的本地智能体 Harness（DeepSeek 驱动，可 SSH 调度 GPU）",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

AGENTS_MD = """# 科研项目：{name}

> 本项目由 [SciForge](https://github.com/sddzlsc/SciForge) 管理。任何 AI Agent（DSH / Claude Code / Codex）
> 在此目录工作时，优先通过 `sciforge` CLI 或 `sciforge` 的 Skill 执行科研任务。

## 项目结构

- `notes/` — 文献调研、精读笔记、创新点、Proposal
- `experiments/` — 可训练代码（建议用 `sciforge exp scaffold` 生成）
- `results/` — 实验指标、图表数据
- `figures/` — 论文级图（SVG/PDF/PNG）
- `paper/` — LaTeX 论文工程
- `refs/` — BibTeX / 参考文献
- `.sciforge/` — 运行记录、会话、pipeline 工件

## 常用命令

```bash
sciforge doctor                                  # 环境自检
sciforge chat "调研 xxx 方向并给出 3 个创新点"     # 交互式科研 agent
sciforge pipeline run --recipe survey --topic "xxx"
sciforge lit search "xxx" --source arxiv
sciforge read 2401.00001 --note
sciforge gpu status                              # 查看 GPU 服务器
sciforge exp scaffold --template cv-classification --name my-exp
sciforge fig arch --spec figures/fig1.yaml
sciforge write draft --venue cvpr
sciforge skills list                             # 可用科研技能
sciforge serve                                   # 本地 Web 控制台
```

## 科研规范（Agent 必读）

1. 先检索后断言；禁止编造论文、引用与实验数字。
2. 每个阶段产出落盘到对应目录，命名可预测。
3. 实验必须记录 seed / 命令 / 环境 / GPU；结果写入 `results/`。
4. 论文中的每个数字必须能追溯到 `results/` 里的真实文件。
5. 远程 GPU 任务用后台运行（tmux/slurm），保留日志。
"""


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------


def _settings(project: Path | None = None) -> Settings:
    try:
        return get_settings(project)
    except ConfigError as exc:
        err_console.print(f"[bold red]配置错误[/bold red]\n{exc}")
        raise typer.Exit(1) from exc


def _short(text: Any, limit: int = 160) -> str:
    body = json.dumps(text, ensure_ascii=False, default=str) if not isinstance(text, str) else text
    body = body.replace("\n", " ")
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _json_out(data: Any) -> None:
    console.print_json(data=data)


def _check_llm_error(exc: Exception) -> None:
    err_console.print(
        Panel(
            f"[bold red]{exc}[/bold red]\n\n"
            "修复建议：\n"
            "  1. `export DEEPSEEK_API_KEY=sk-...`（或在 ~/.sciforge/config.toml 配置）\n"
            "  2. `sciforge llm test` 验证连通性\n"
            "  3. 完全离线体验：加 `--mock` 参数\n"
            "  4. 使用其他 OpenAI 兼容服务：参考 docs/LLM_PROVIDERS.md",
            title="LLM 调用失败",
            border_style="red",
        )
    )


class ConsoleEventHandler:
    """把 EventBus 事件渲染成 CLI 输出。"""

    def __init__(self, *, stream: bool = False) -> None:
        self.stream = stream
        self._printed_delta = False
        self._last_tool_line = False

    def __call__(self, event: Event) -> None:
        kind = event.kind
        payload = event.payload
        if kind == "tool_call":
            self._last_tool_line = True
            console.print(
                f"\n[bold cyan]⏺ {payload.get('name')}[/bold cyan] "
                f"[dim]{_short(payload.get('arguments') or {}, 180)}[/dim]"
            )
        elif kind == "tool_result":
            ok = payload.get("ok")
            icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
            elapsed = payload.get("elapsed", "")
            console.print(f"{icon} [dim]{payload.get('name')} · {elapsed}s[/dim]")
            if not ok:
                err = (payload.get("error") or payload.get("content") or "").strip()
                console.print(f"  [red]{_short(err, 260)}[/red]")
            else:
                preview = (payload.get("content") or "").strip().splitlines()
                for line in preview[:2]:
                    console.print(f"  [dim]{_short(line, 200)}[/dim]")
        elif kind == "llm_delta":
            text = payload.get("text") or ""
            if text:
                self._printed_delta = True
                console.print(text, end="", soft_wrap=True, highlight=False)
        elif kind == "assistant":
            if payload.get("content") and self.stream:
                console.print()
        elif kind == "stage":
            phase = payload.get("phase")
            if phase == "start":
                console.print(
                    f"\n[bold magenta]▶ [{payload.get('index')}/{payload.get('total')}] "
                    f"{payload.get('title')}[/bold magenta]"
                )
            elif phase == "end":
                icon = "[green]✓[/green]" if payload.get("ok") else "[red]✗[/red]"
                console.print(
                    f"{icon} [bold]{payload.get('title')}[/bold] "
                    f"[dim]{payload.get('duration')}s[/dim]"
                )
            elif phase == "run_start":
                console.print(
                    Panel(
                        f"Recipe：[bold]{payload.get('recipe')}[/bold]\n"
                        f"主题：{payload.get('topic')}\n"
                        f"阶段：{', '.join(payload.get('stages') or [])}",
                        title="SciForge Pipeline",
                        border_style="magenta",
                    )
                )
            elif phase == "agent_end":
                usage = payload.get("usage") or {}
                console.print(
                    f"[dim]steps={payload.get('steps')} tool_calls={payload.get('tool_calls')} "
                    f"tokens={usage.get('total_tokens')}[/dim]"
                )
        elif kind == "error":
            console.print(f"\n[bold red]✗ {payload.get('message')}[/bold red]")
        elif kind == "artifact":
            files = payload.get("files") or []
            if files:
                console.print("[dim]artifacts: " + ", ".join(Path(f).name for f in files) + "[/dim]")


def _build_agent(
    settings: Settings, project: Path | None, *, mock: bool, stream: bool, max_steps: int | None
):
    from sciforge.agent.runtime import make_agent, make_bus

    bus = make_bus(ConsoleEventHandler(stream=stream))
    return make_agent(
        settings,
        project_dir=project,
        bus=bus,
        mock=mock,
        stream=stream,
        max_steps=max_steps,
    )


# ---------------------------------------------------------------------------
# 基础命令
# ---------------------------------------------------------------------------


@app.command("version")
def version() -> None:
    """打印版本信息。"""
    console.print(f"SciForge [bold cyan]{sciforge.__version__}[/bold cyan] · Python {sys.version.split()[0]}")


@app.command("init")
def init(
    directory: Path | None = typer.Argument(None, help="项目目录，默认当前目录"),
    name: str | None = typer.Option(None, "--name", "-n", help="项目名"),
    topic: str | None = typer.Option(None, "--topic", "-t", help="研究主题（写入项目说明）"),
    force: bool = typer.Option(False, "--force", help="已存在时覆盖项目配置"),
    install_skills: bool = typer.Option(False, "--install-skills", help="同时安装 Skill 到 DSH"),
) -> None:
    """初始化科研项目：目录结构 + 项目配置 + AGENTS.md + 全局配置。"""
    dest = Path(directory or Path.cwd()).expanduser().resolve()
    project_cfg = project_config_path(dest)
    if project_cfg.exists() and not force:
        err_console.print(
            f"[yellow]项目已初始化：{project_cfg}[/yellow]\n如需重建请加 --force"
        )
        raise typer.Exit(1)
    dest.mkdir(parents=True, exist_ok=True)

    settings = _settings(dest)
    settings.project_name = name or dest.name
    for sub in ("notes", "experiments", "results", "figures", "paper", "refs", "data"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    (dest / ".sciforge").mkdir(exist_ok=True)
    save_project_config(dest, settings)

    agents = dest / "AGENTS.md"
    if not agents.exists() or force:
        agents.write_text(AGENTS_MD.format(name=settings.project_name), encoding="utf-8")
    readme = dest / "README.md"
    if not readme.exists() or force:
        readme.write_text(
            f"# {settings.project_name}\n\n"
            f"研究主题：{topic or '（待填写）'}\n\n"
            "本项目管理命令：\n\n```bash\nsciforge doctor\nsciforge chat \"...\"\n"
            "sciforge pipeline run --recipe full --topic \"...\"\n```\n",
            encoding="utf-8",
        )

    # 全局配置：不存在时生成模板
    global_cfg = global_config_path()
    created_global = False
    if not global_cfg.exists():
        global_cfg.parent.mkdir(parents=True, exist_ok=True)
        global_cfg.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        try:
            global_cfg.chmod(0o600)
        except OSError:
            pass
        created_global = True

    if install_skills:
        _install_bundled_skills("dsh")

    console.print(
        Panel(
            f"项目：[bold]{settings.project_name}[/bold]\n"
            f"目录：{dest}\n"
            f"配置：{project_cfg}\n"
            f"全局配置：{global_cfg}{'（已生成模板）' if created_global else ''}\n\n"
            "下一步：\n"
            "  1. `export DEEPSEEK_API_KEY=sk-...`\n"
            "  2. `sciforge doctor` 自检\n"
            "  3. `sciforge chat \"调研 …\"` 或 `sciforge pipeline run --recipe survey --topic \"…\"`\n"
            "  4. 离线体验：`sciforge demo`",
            title="[green]SciForge 项目初始化完成[/green]",
            border_style="green",
        )
    )


def _install_bundled_skills(target: str = "dsh") -> int:
    try:
        from sciforge.skills_manager import SkillManager
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Skill 模块不可用：{exc}[/yellow]")
        return 0
    manager = SkillManager()
    count = 0
    for skill in manager.list():
        if skill.source != "bundled":
            continue
        try:
            manager.install_to(skill.name, target)
            count += 1
        except Exception:  # noqa: BLE001
            continue
    console.print(f"[green]已安装 {count} 个 Skill 到 {target}[/green]")
    return count


@app.command("doctor")
def doctor(
    project: Path | None = typer.Option(None, "--project", "-p", help="项目目录"),
    network: bool = typer.Option(False, "--network", help="测试 SSH 服务器连通性（可能较慢）"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """环境自检：配置 / LLM / 工具 / 服务器 / Skill / LaTeX / Web。"""
    settings = _settings(project)
    checks: dict[str, Any] = {}
    table = Table(title="SciForge Doctor", show_lines=False, header_style="bold cyan")
    table.add_column("检查项", style="bold")
    table.add_column("状态")
    table.add_column("详情", overflow="fold")

    def add(name: str, ok: bool | None, detail: str) -> None:
        checks[name] = {"ok": ok, "detail": detail}
        status = "[green]✓[/green]" if ok else ("[yellow]○[/yellow]" if ok is None else "[red]✗[/red]")
        table.add_row(name, status, detail)

    # Python / 包
    py_ok = sys.version_info >= (3, 10)
    add("Python", py_ok, f"{sys.version.split()[0]}（要求 >=3.10）")
    try:
        import typer  # noqa: F401

        add("核心依赖", True, "httpx / pydantic / typer 可用")
    except Exception as exc:  # noqa: BLE001
        add("核心依赖", False, str(exc))

    # 路径
    home = data_home()
    ensure_dirs(settings)
    add("数据目录", home.exists(), str(home))
    gcfg = global_config_path()
    add("全局配置", gcfg.exists(), str(gcfg) + ("" if gcfg.exists() else "（运行 sciforge init 生成）"))
    if settings.project_dir:
        add("项目", True, f"{settings.project_name} @ {settings.project_dir}")
    else:
        add("项目", None, "当前目录不是 SciForge 项目（可运行 sciforge init）")

    # LLM
    try:
        from sciforge.llm.router import LLMRouter

        router = LLMRouter(settings)
        for role in ("planner", "executor", "writer"):
            info = router.check(role)
            add(
                f"LLM/{role}",
                bool(info.get("ok")),
                f"{info.get('provider')} · {info.get('model')}"
                + ("" if info.get("ok") else f" · {info.get('error')}"),
            )
    except Exception as exc:  # noqa: BLE001
        add("LLM", False, str(exc))

    # 工具
    try:
        from sciforge.tools import import_errors, tool_health

        health = tool_health()
        add("工具", health["count"] > 0, f"{health['count']} 个已注册；分类 {health['by_tag']}")
        if import_errors:
            add("工具模块", None, "未就绪：" + ", ".join(import_errors.keys()))
    except Exception as exc:  # noqa: BLE001
        add("工具", False, str(exc))

    # 服务器
    if settings.servers:
        details = []
        for server in settings.servers:
            detail = f"{server.name}: {server.user}@{server.host}:{server.port}"
            if network:
                try:
                    from sciforge.tools.remote import SSHExecutor

                    result = SSHExecutor(server).check()
                    ok = bool(result.get("ok"))
                    detail += " ✓" if ok else f" ✗ {result.get('error', '')[:80]}"
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    detail += f" ✗ {exc}"
                add(f"服务器/{server.name}", ok, detail)
            else:
                details.append(detail)
        if not network:
            add("GPU 服务器", True, "；".join(details))
    else:
        add("GPU 服务器", None, "未配置（sciforge ssh add 或编辑 ~/.sciforge/config.toml）")

    # Skill / 文献库
    try:
        from sciforge.skills_manager import SkillManager

        skills = SkillManager(settings).list()
        add("Skills", len(skills) > 0, f"{len(skills)} 个可用")
    except Exception as exc:  # noqa: BLE001
        add("Skills", None, f"暂不可用：{exc}")
    try:
        from sciforge.library import PaperLibrary

        papers = PaperLibrary(data_home()).list(limit=1000)
        add("文献库", True, f"{len(papers)} 篇")
    except Exception as exc:  # noqa: BLE001
        add("文献库", None, f"暂不可用：{exc}")

    # LaTeX / Web
    latex = shutil.which("latexmk") or shutil.which("xelatex") or shutil.which("pdflatex")
    add("LaTeX", bool(latex), latex or "未安装（论文编译需要 TeX Live）")
    try:
        import uvicorn  # noqa: F401

        add("Web", True, "FastAPI / uvicorn 可用（sciforge serve）")
    except Exception as exc:  # noqa: BLE001
        add("Web", None, f"未安装：{exc}")

    if os.environ.get("SCIFORGE_MOCK", "").lower() in ("1", "true", "yes"):
        add("Mock 模式", True, "SCIFORGE_MOCK=1（不调用真实 LLM）")

    if json_output:
        _json_out(checks)
        return
    console.print(table)
    failures = [name for name, item in checks.items() if item["ok"] is False]
    warnings = [name for name, item in checks.items() if item["ok"] is None]
    if failures:
        console.print(f"\n[bold red]{len(failures)} 项需要修复：[/bold red] {', '.join(failures)}")
        console.print("[dim]提示：LLM 未配置可用 `sciforge demo` 或 `--mock` 离线体验。[/dim]")
    elif warnings:
        console.print(f"\n[green]整体可用[/green]，{len(warnings)} 项可选：{', '.join(warnings)}")
    else:
        console.print("\n[bold green]全部检查通过 ✓[/bold green]")


# ---------------------------------------------------------------------------
# Agent / chat
# ---------------------------------------------------------------------------


@app.command("chat")
def chat(
    message: str | None = typer.Argument(None, help="一次性任务；省略则进入交互模式"),
    project: Path | None = typer.Option(None, "--project", "-p"),
    mock: bool = typer.Option(False, "--mock", help="离线 Mock 模式（不调用真实 LLM）"),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="流式输出"),
    max_steps: int | None = typer.Option(
        None, "--max-steps", help="单次对话最大 Agent 步数；0 = 无上限（谨慎）"
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """与科研 Agent 对话（可调用文献/GPU/实验/写作/图表工具）。"""
    settings = _settings(project)
    from sciforge.llm.base import LLMError

    def run_once(text: str) -> Any:
        agent, ctx, _router = _build_agent(
            settings, project, mock=mock, stream=stream, max_steps=max_steps
        )
        try:
            return agent.run(text)
        except LLMError as exc:
            _check_llm_error(exc)
            raise typer.Exit(1) from exc

    if message:
        result = run_once(message)
        if json_output:
            _json_out(
                {
                    "content": result.content,
                    "steps": result.steps,
                    "tool_calls": result.tool_calls,
                    "usage": result.usage.as_dict(),
                    "artifacts": result.artifacts,
                    "error": result.error,
                }
            )
        else:
            if not stream:
                console.print(Markdown(result.content or "（无输出）"))
            console.print(
                f"\n[dim]steps={result.steps} tool_calls={result.tool_calls} "
                f"tokens={result.usage.total_tokens} · session={result.session_path}[/dim]"
            )
            if result.artifacts:
                console.print("[dim]产出：" + "、".join(Path(a).name for a in result.artifacts) + "[/dim]")
        if result.error:
            raise typer.Exit(1)
        return

    # 交互模式
    console.print(
        Panel(
            f"SciForge 科研 Agent · 模型 {settings.llm.roles.get('executor', {}).model if hasattr(settings.llm.roles.get('executor'), 'model') else 'deepseek'}"
            f"{' · [yellow]离线 Mock[/yellow]' if mock else ''}\n"
            "[dim]/tools 查看工具 · /mock 切换离线 · /new 新会话 · /exit 退出[/dim]",
            title="[bold cyan]交互模式[/bold cyan]",
            border_style="cyan",
        )
    )
    history: list = []
    while True:
        try:
            text = console.input("\n[bold cyan]你 > [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见[/dim]")
            break
        if not text:
            continue
        if text in ("/exit", "/quit", "exit", "quit"):
            break
        if text == "/tools":
            _print_tools()
            continue
        if text == "/new":
            history = []
            console.print("[dim]已开始新上下文[/dim]")
            continue
        if text == "/mock":
            mock = not mock
            console.print(f"[yellow]Mock 模式：{'开' if mock else '关'}[/yellow]")
            continue
        agent, ctx, _router = _build_agent(
            settings, project, mock=mock, stream=stream, max_steps=max_steps
        )
        try:
            result = agent.run(text, history=history or None)
        except LLMError as exc:
            _check_llm_error(exc)
            continue
        history = result.messages[1:]
        if not stream:
            console.print(Markdown(result.content or "（无输出）"))
        if result.artifacts:
            console.print("[dim]产出：" + "、".join(Path(a).name for a in result.artifacts) + "[/dim]")


def _print_tools() -> None:
    table = Table(title="可用工具", header_style="bold cyan")
    table.add_column("名称", style="bold")
    table.add_column("分类")
    table.add_column("说明", overflow="fold")
    for tool in list_tools():
        table.add_row(tool.name, ", ".join(tool.tags), tool.description.split("\n")[0][:100])
    console.print(table)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

pipeline_app = typer.Typer(help="科研流水线（可组合 Recipe）", no_args_is_help=True)
app.add_typer(pipeline_app, name="pipeline")


@pipeline_app.command("list")
def pipeline_list(json_output: bool = typer.Option(False, "--json")) -> None:
    """列出内置 Recipe。"""
    from sciforge.pipeline.recipes import list_recipes

    recipes = list_recipes()
    if json_output:
        _json_out(recipes)
        return
    table = Table(title="SciForge Recipes", header_style="bold magenta")
    table.add_column("Recipe", style="bold")
    table.add_column("说明", overflow="fold")
    table.add_column("阶段")
    for item in recipes:
        table.add_row(item["name"], f"{item['title']} — {item['description']}", item["stages"])
    console.print(table)


@pipeline_app.command("run")
def pipeline_run(
    recipe: str = typer.Option("full", "--recipe", "-r", help="recipe 名称，见 pipeline list"),
    topic: str = typer.Option(..., "--topic", "-t", help="研究主题"),
    project: Path | None = typer.Option(None, "--project", "-p"),
    until: str | None = typer.Option(None, "--until", help="跑到指定阶段后停止"),
    server: str | None = typer.Option(None, "--server", help="指定 GPU 服务器名（默认自动选择）"),
    dataset: str | None = typer.Option(
        None, "--dataset", help="本地数据集路径（默认自动探测项目 data/ 目录）"
    ),
    stage: str | None = typer.Option(None, "--stage", help="只运行 full 流程中的某一个阶段"),
    step_mode: bool = typer.Option(
        False, "--step-mode", help="单步模式：每个阶段开始前暂停，按继续执行下一步"
    ),
    mock: bool = typer.Option(False, "--mock", help="离线 Mock 模式"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """运行科研流水线：课题定义 → 调研 → 精读 → 创新 → GPU/数据 → 实验 → 配图 → 论文。"""
    settings = _settings(project)
    from sciforge.agent.runtime import make_bus
    from sciforge.pipeline.engine import PipelineRunner
    from sciforge.pipeline.recipes import get_recipe

    if get_recipe(recipe) is None:
        err_console.print(f"[red]未知 recipe：{recipe}[/red]")
        raise typer.Exit(1)
    bus = make_bus(ConsoleEventHandler())
    runner = PipelineRunner(settings, project, mock=mock, bus=bus)
    try:
        run = runner.run(
            recipe, topic, until=until, server=server, dataset=dataset,
            stage=stage, step_mode=step_mode,
        )
    except KeyboardInterrupt as exc:
        err_console.print("[yellow]已中断[/yellow]")
        raise typer.Exit(130) from exc
    if json_output:
        _json_out(run.to_dict())
        return
    console.print(
        Panel(
            f"Run：`{run.run_id}`\n状态：[bold]{run.status}[/bold]\n"
            f"耗时：{round((run.finished_at or time.time()) - run.started_at, 1)}s\n"
            f"报告：{run.run_dir / 'REPORT.md'}",
            title="[green]Pipeline 完成[/green]" if run.ok or run.status == "partial" else "[red]Pipeline 结束[/red]",
            border_style="green" if run.ok else "yellow",
        )
    )
    if run.error:
        err_console.print(f"[red]{run.error}[/red]")
        raise typer.Exit(1)


@pipeline_app.command("runs")
def pipeline_runs(
    project: Path | None = typer.Option(None, "--project", "-p"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """列出历史运行。"""
    from sciforge.pipeline.engine import list_runs

    settings = _settings(project)
    root = Path(project or settings.project_dir or Path.cwd())
    runs = list_runs(root)
    if json_output:
        _json_out(runs)
        return
    if not runs:
        console.print("[dim]暂无运行记录[/dim]")
        return
    table = Table(title="Pipeline Runs", header_style="bold magenta")
    for column in ("Run ID", "Recipe", "主题", "状态", "阶段"):
        table.add_column(column, overflow="fold")
    for item in runs[:30]:
        table.add_row(
            item.get("run_id", ""),
            item.get("recipe", ""),
            _short(item.get("topic", ""), 40),
            item.get("status", ""),
            str(len(item.get("stages", []))),
        )
    console.print(table)


@pipeline_app.command("show")
def pipeline_show(
    run_id: str = typer.Argument(..., help="Run ID"),
    project: Path | None = typer.Option(None, "--project", "-p"),
) -> None:
    """查看一次运行的报告。"""
    from sciforge.pipeline.engine import load_run

    settings = _settings(project)
    root = Path(project or settings.project_dir or Path.cwd())
    run = load_run(root, run_id)
    if not run:
        err_console.print(f"[red]找不到 run：{run_id}[/red]")
        raise typer.Exit(1)
    report = Path(run["run_dir"]) / "REPORT.md"
    if report.exists():
        console.print(Markdown(report.read_text(encoding="utf-8")))
    else:
        _json_out(run)


# ---------------------------------------------------------------------------
# Demo / Serve
# ---------------------------------------------------------------------------


@app.command("demo")
def demo(
    directory: Path | None = typer.Argument(None, help="演示项目输出目录（默认 ./sciforge-demo）"),
    recipe: str = typer.Option("survey", "--recipe", "-r", help="演示的 recipe（默认 survey）"),
    topic: str = typer.Option(
        "面向长尾与分布偏移的视觉模型自适应计算",
        "--topic",
        "-t",
        help="演示研究主题",
    ),
    open_web: bool = typer.Option(False, "--web", help="演示完成后启动 Web 控制台"),
) -> None:
    """离线演示：无需 API key，跑通 文献→精读→创新点→配图→论文 骨架。"""
    from sciforge.demo import run_demo

    dest = Path(directory or Path.cwd() / "sciforge-demo").expanduser().resolve()
    try:
        summary = run_demo(dest, recipe=recipe, topic=topic)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]演示失败：{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(Markdown(summary["markdown"]))
    console.print(f"\n[green]演示项目：[/green]{dest}")
    if open_web:
        _serve(dest, None, None)


def _serve(
    project: Path | None,
    host: str | None,
    port: int | None,
    open_browser: bool | None = None,
) -> None:
    try:
        from sciforge.web.server import run_server
    except Exception as exc:  # noqa: BLE001
        err_console.print(
            f"[red]Web 模块不可用：{exc}[/red]\n请安装：`uv pip install -e '.[web]'`"
        )
        raise typer.Exit(1) from exc
    settings = _settings(project)
    if open_browser is not None:
        settings.web.open_browser = open_browser
    run_server(host=host, port=port, settings=settings)


@app.command("serve")
def serve(
    project: Path | None = typer.Option(None, "--project", "-p"),
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    open_browser: bool | None = typer.Option(
        None, "--open/--no-open", help="启动后自动打开浏览器"
    ),
) -> None:
    """启动本地 Web 控制台（默认 http://127.0.0.1:8765）。"""
    _serve(project, host, port, open_browser)


@app.command("gui")
def gui(
    project: Path | None = typer.Option(None, "--project", "-p", help="项目目录，默认当前目录"),
    port: int = typer.Option(8765, "--port"),
) -> None:
    """一键启动可视化 GUI：本地网页控制台并自动打开浏览器。"""
    console.print(f"[cyan]SciForge GUI[/cyan] → 启动本地控制台（端口 {port}）…")
    _serve(project, None, port, open_browser=True)


# ---------------------------------------------------------------------------
# Config / Tools / LLM / DSH
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="查看/初始化配置", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("path")
def config_path() -> None:
    """打印配置文件路径。"""
    console.print(f"全局配置：{global_config_path()}")
    console.print(f"数据目录：{data_home()}")
    settings = _settings()
    if settings.project_dir:
        console.print(f"项目配置：{project_config_path(settings.project_dir)}")


@config_app.command("show")
def config_show(
    project: Path | None = typer.Option(None, "--project", "-p"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """显示合并后的配置（密钥已脱敏）。"""
    settings = _settings(project)
    data = settings.model_dump(mode="json", exclude={"project_dir"})
    for _name, provider in (data.get("llm", {}).get("providers") or {}).items():
        if provider.get("api_key"):
            provider["api_key"] = "***"
    data["_meta"] = {
        "project_dir": str(settings.project_dir) if settings.project_dir else None,
        "data_home": str(data_home()),
        "global_config": str(global_config_path()),
    }
    if json_output:
        _json_out(data)
    else:
        import yaml

        console.print(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


@config_app.command("init")
def config_init(force: bool = typer.Option(False, "--force")) -> None:
    """生成全局配置模板（~/.sciforge/config.toml）。"""
    path = global_config_path()
    if path.exists() and not force:
        console.print(f"[yellow]已存在：{path}（--force 覆盖）[/yellow]")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    console.print(f"[green]已写入 {path}[/green]")


tools_app = typer.Typer(help="工具浏览与直接调用", no_args_is_help=True)
app.add_typer(tools_app, name="tools")


@tools_app.command("list")
def tools_list(
    tag: str | None = typer.Option(None, "--tag", help="按分类过滤"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """列出全部工具。"""
    from sciforge.tools import import_errors

    tools = list_tools([tag] if tag else None)
    if json_output:
        _json_out(
            {
                "tools": [
                    {"name": t.name, "tags": list(t.tags), "description": t.description} for t in tools
                ],
                "import_errors": import_errors,
            }
        )
        return
    _print_tools()
    if import_errors:
        console.print(f"[yellow]未就绪模块：{', '.join(import_errors)}[/yellow]")


@tools_app.command("run")
def tools_run(
    name: str = typer.Argument(..., help="工具名"),
    args: str = typer.Option("{}", "--args", help="JSON 参数"),
    project: Path | None = typer.Option(None, "--project", "-p"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """直接调用一个工具（调试/自动化用）。"""
    from sciforge.agent.runtime import make_ctx

    settings = _settings(project)
    try:
        payload = json.loads(args) if args.strip() else {}
    except json.JSONDecodeError as exc:
        err_console.print(f"[red]--args 不是合法 JSON：{exc}[/red]")
        raise typer.Exit(1) from exc
    ctx = make_ctx(settings, project_dir=project)
    result = run_tool(name, ctx, payload)
    if json_output:
        _json_out(
            {
                "ok": result.ok,
                "content": result.content,
                "data": result.data,
                "artifacts": result.artifacts,
                "error": result.error,
            }
        )
    else:
        console.print(Markdown(result.content))
        if result.artifacts:
            console.print("[dim]artifacts: " + ", ".join(result.artifacts) + "[/dim]")
    if not result.ok:
        raise typer.Exit(1)


llm_app = typer.Typer(help="LLM 连通性与配置", no_args_is_help=True)
app.add_typer(llm_app, name="llm")


@llm_app.command("test")
def llm_test(
    role: str = typer.Option("executor", "--role"),
    prompt: str = typer.Option("用一句话说明你是谁。", "--prompt"),
    mock: bool = typer.Option(False, "--mock"),
) -> None:
    """测试指定角色的 LLM 是否可用。"""
    settings = _settings()
    from sciforge.llm.base import LLMError
    from sciforge.llm.router import LLMRouter

    router = LLMRouter(settings, force_mock=mock)
    info = router.check(role)
    console.print(f"provider=[cyan]{info.get('provider')}[/cyan] model=[cyan]{info.get('model')}[/cyan]")
    try:
        started = time.time()
        text = router.complete(role, prompt)
        console.print(f"[green]✓ {round(time.time() - started, 2)}s[/green]")
        console.print(Markdown(text))
        console.print(f"[dim]usage: {router.usage.as_dict()}[/dim]")
    except LLMError as exc:
        _check_llm_error(exc)
        raise typer.Exit(1) from exc


@llm_app.command("providers")
def llm_providers(json_output: bool = typer.Option(False, "--json")) -> None:
    """列出已配置的 LLM providers。"""
    settings = _settings()
    rows = []
    for name, provider in settings.llm.providers.items():
        rows.append(
            {
                "name": name,
                "base_url": provider.base_url,
                "default_model": provider.default_model,
                "models": provider.models,
                "key": "已配置" if settings.resolve_api_key(name) else f"缺少 {provider.api_key_env}",
            }
        )
    if json_output:
        _json_out(rows)
        return
    table = Table(title="LLM Providers", header_style="bold cyan")
    for column in ("名称", "base_url", "默认模型", "密钥"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(row["name"], row["base_url"], row["default_model"], row["key"])
    console.print(table)


dsh_app = typer.Typer(help="与 DeepSeek Harness（DSH）集成", no_args_is_help=True)
app.add_typer(dsh_app, name="dsh")


@dsh_app.command("install")
def dsh_install(
    name: str | None = typer.Option(None, "--name", help="只安装指定 skill"),
) -> None:
    """把 SciForge 科研 Skill 安装到 ~/.dsh/skills，供 DSH 直接调用。"""
    try:
        from sciforge.skills_manager import SkillManager
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]Skill 模块不可用：{exc}[/red]")
        raise typer.Exit(1) from exc
    manager = SkillManager()
    skills = [manager.get(name)] if name else manager.list()
    count = 0
    for skill in skills:
        if skill is None:
            continue
        try:
            manager.install_to(skill.name, "dsh")
            count += 1
        except Exception as exc:  # noqa: BLE001
            err_console.print(f"[yellow]{skill.name}: {exc}[/yellow]")
    console.print(
        f"[green]已安装 {count} 个 skill 到 ~/.dsh/skills[/green]\n"
        "新开一个 DSH 会话后即可在技能列表中看到并使用（例如 sciforge-harness / literature-survey）。"
    )


@dsh_app.command("status")
def dsh_status() -> None:
    """检查 DSH 技能安装状态。"""
    target = Path("~/.dsh/skills").expanduser()
    if not target.exists():
        console.print("[yellow]未找到 ~/.dsh/skills（DSH 可能未安装）[/yellow]")
        return
    installed = sorted(p.name for p in target.iterdir() if (p / "SKILL.md").exists())
    console.print(f"DSH 技能目录：{target}")
    console.print(f"共 {len(installed)} 个技能：" + ", ".join(installed))


# ---------------------------------------------------------------------------
# 模块子命令自动注册
# ---------------------------------------------------------------------------

_MODULE_CLI = [
    "sciforge.tools.literature",
    "sciforge.tools.paper_reader",
    "sciforge.tools.remote",
    "sciforge.tools.experiments",
    "sciforge.tools.figures",
    "sciforge.tools.writing",
    "sciforge.tools.skills",
    "sciforge.tools.ideation",
]


def _register_module_clis() -> None:
    import importlib

    for module_name in _MODULE_CLI:
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - 模块尚未就绪不影响其它命令
            continue
        register = getattr(module, "add_cli", None) or getattr(module, "register", None)
        if callable(register):
            try:
                register(app)
            except Exception as exc:  # noqa: BLE001
                err_console.print(f"[yellow]CLI 注册失败 {module_name}: {exc}[/yellow]")


_register_module_clis()


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
