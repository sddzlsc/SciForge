"""可组合科研流水线引擎。

一个 Recipe = 有序 Stage 列表；每个 Stage 交给 AgentLoop 执行，产出 Markdown 工件与
可选文件，并把上下文传递给下游 Stage。Stage 之间通过 ``.sciforge/runs/<run_id>/``
落盘解耦，因此可以 --until 中途停止、稍后从工件继续。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from sciforge.agent.loop import AgentLoop
from sciforge.agent.prompts import load_stage_prompt, render_prompt
from sciforge.config import Settings, ensure_dirs, get_settings
from sciforge.core.events import EventBus
from sciforge.core.registry import ToolContext
from sciforge.llm.router import LLMRouter
from sciforge.pipeline.control import RunControl
from sciforge.pipeline.recipes import RECIPES, get_recipe, stage_recipe
from sciforge.pipeline.types import PipelineRun, Recipe, Stage, StageResult

STAGE_EVENT_PREFIX = "stage"


class PipelineRunner:
    def __init__(
        self,
        settings: Settings | None = None,
        project_dir: str | Path | None = None,
        *,
        router: LLMRouter | None = None,
        mock: bool | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.settings = settings or get_settings(project_dir)
        self.project_dir = Path(
            project_dir or self.settings.project_dir or Path.cwd()
        ).expanduser().resolve()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = ensure_dirs(self.settings)
        self.router = router or LLMRouter(self.settings, force_mock=mock)
        self.mock = bool(mock if mock is not None else self.router.force_mock)
        self.bus = bus or EventBus()
        self.control = RunControl()

    # ------------------------------------------------------------------
    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.bus.emit(kind, payload)

    def _make_run_dir(self, recipe_name: str) -> tuple[str, Path]:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{recipe_name}-{uuid.uuid4().hex[:4]}"
        run_dir = self.project_dir / ".sciforge" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_id, run_dir

    def _write_manifest(self, run: PipelineRun) -> None:
        try:
            (run.run_dir / "run.json").write_text(
                json.dumps(run.to_dict(), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ------------------------------------------------------------------
    def _collect_context(self, run: PipelineRun, stage: Stage, limit: int = 14000) -> str:
        """把上游工件压缩成后续 stage 的上下文。"""
        chunks: list[str] = []
        for result in reversed(run.stages):
            if not result.ok:
                continue
            body = result.content.strip()
            if body:
                chunks.append(f"### {result.title}\n{body}")
            for artifact in result.artifacts:
                path = Path(artifact)
                if path.exists() and path.suffix in (".md", ".txt", ".yaml", ".yml", ".json"):
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if len(text) > 2500:
                        text = text[:2500] + "\n…（截断）"
                    chunks.append(f"### 文件 {path.name}\n{text}")
            if sum(len(c) for c in chunks) > limit:
                break
        if not chunks:
            return "（无上游产出；请基于研究主题自行完成本阶段。）"
        joined = "\n\n".join(chunks)
        return joined[-limit:] if len(joined) > limit else joined

    def _project_snapshot(self, limit: int = 3000) -> str:
        lines = []
        ignore = {".git", ".venv", "__pycache__", ".sciforge", "node_modules"}
        for path in sorted(self.project_dir.rglob("*")):
            if any(part in ignore for part in path.parts):
                continue
            rel = path.relative_to(self.project_dir)
            if path.is_file() and path.stat().st_size < 2_000_000:
                lines.append(f"{rel} ({path.stat().st_size}B)")
            if len(lines) >= 60:
                break
        return "\n".join(lines) if lines else "（空项目）"

    # ------------------------------------------------------------------
    def _run_stage(self, run: PipelineRun, stage: Stage, index: int, total: int) -> StageResult:
        stage_dir = run.run_dir / f"{index:02d}-{stage.name}"
        session_dir = stage_dir / "session"
        session_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()

        self._emit(
            "stage",
            {
                "phase": "start",
                "index": index,
                "total": total,
                "name": stage.name,
                "title": stage.title,
                "run_id": run.run_id,
            },
        )

        try:
            template = load_stage_prompt(stage.prompt, self.settings.agent.language)
        except Exception:  # noqa: BLE001 - 找不到专用 prompt 时用通用 prompt
            template = (
                f"# {stage.title}\n\n研究主题：{{{{topic}}}}\n\n"
                "## 上游产出\n{{context}}\n\n## 项目现状\n{{project}}\n\n"
                "请完成本阶段任务，并将结构化结果写入最终回答（Markdown）。"
            )

        prompt = render_prompt(
            template,
            topic=run.topic,
            context=self._collect_context(run, stage),
            project=self._project_snapshot(),
            title=stage.title,
            server=run.server or "（未指定，自动选择）",
            dataset=run.dataset or "（未指定，自动探测项目 data/ 目录）",
        )

        def emit(kind: str, payload: dict[str, Any]) -> None:
            self._emit(kind, payload)

        ctx = ToolContext(
            settings=self.settings,
            project_dir=self.project_dir,
            data_dir=self.data_dir,
            session_dir=stage_dir,
            emit=emit,
            approve=lambda name, args: True,
        )
        ctx.extra.update(
            {
                "router": self.router,
                "run_dir": run.run_dir,
                "stage": stage.name,
                "mock": self.mock,
            }
        )

        agent = AgentLoop(
            self.router,
            ctx,
            system_prompt=self._system_prompt(),
            tools=stage.tools or None,
            role=stage.role,
            max_steps=stage.max_steps,
            approval="auto",
            bus=self.bus,
            temperature=self.settings.agent.temperature,
            control=self.control,
        )
        result = agent.run(prompt)

        content = result.content.strip()
        (stage_dir / "output.md").write_text(content + "\n", encoding="utf-8")

        artifacts = [str(stage_dir / "output.md")]
        for expected in stage.artifacts:
            candidate = stage_dir / expected
            if candidate.exists():
                artifacts.append(str(candidate))
        for artifact in result.artifacts:
            path = Path(artifact)
            if path.exists() and str(path) not in artifacts:
                artifacts.append(str(path))

        ok = result.error is None and bool(content)
        stage_result = StageResult(
            name=stage.name,
            title=stage.title,
            ok=ok,
            content=content,
            artifacts=artifacts,
            usage=result.usage.as_dict(),
            error=result.error,
            duration=time.time() - started,
        )
        self._emit(
            "stage",
            {
                "phase": "end",
                "index": index,
                "total": total,
                "name": stage.name,
                "title": stage.title,
                "run_id": run.run_id,
                "ok": ok,
                "duration": round(stage_result.duration, 2),
                "artifacts": artifacts,
                "error": stage_result.error,
            },
        )
        self._emit("artifact", {"stage": stage.name, "files": artifacts})
        return stage_result

    def _system_prompt(self) -> str:
        from sciforge.agent.prompts import load_system_prompt

        base = load_system_prompt(self.settings.agent.language)
        extra = (
            "\n\n## 当前模式：Pipeline 阶段执行\n"
            "你正在执行科研流水线中的某个阶段。请专注完成本阶段目标，产出结构化 Markdown；"
            "需要落盘的文件直接用 write_file 写到当前目录（相对路径即可）。"
            "阶段末尾给出：结论 / 产出文件 / 下一阶段的输入建议。"
        )
        if self.mock:
            extra += "\n\n> 注意：当前为离线 Mock 模式，请在回答开头标注「离线模式」。"
        return base + extra

    # ------------------------------------------------------------------
    def pause(self) -> bool:
        return self.control.pause()

    def resume(self) -> None:
        self.control.resume()

    def stop(self) -> None:
        self.control.stop()

    def run(
        self,
        recipe: str | Recipe,
        topic: str,
        *,
        until: str | None = None,
        run_id: str | None = None,
        skip_stages: list[str] | None = None,
        server: str | None = None,
        dataset: str | None = None,
        stage: str | None = None,
        step_mode: bool = False,
    ) -> PipelineRun:
        if stage:
            recipe_obj = stage_recipe(stage)
            if recipe_obj is None:
                raise ValueError(f"未知阶段: {stage}")
        else:
            recipe_obj = get_recipe(recipe) if isinstance(recipe, str) else recipe
        if recipe_obj is None:
            raise ValueError(f"未知 recipe: {recipe}（可用: {', '.join(RECIPES)}）")

        run_id, run_dir = self._make_run_dir(recipe_obj.name)
        if run_id is None:  # pragma: no cover
            run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{recipe_obj.name}"
        run = PipelineRun(
            run_id=run_id,
            recipe=recipe_obj.name,
            topic=topic,
            run_dir=run_dir,
            server=(server or "").strip() or None,
            dataset=(dataset or "").strip() or None,
            stage=stage,
            step_mode=bool(step_mode),
        )
        inputs = f"# 研究主题\n\n{topic}\n"
        if run.server:
            inputs += f"\n- 指定服务器：`{run.server}`\n"
        if run.dataset:
            inputs += f"- 指定数据集：`{run.dataset}`\n"
        (run_dir / "topic.md").write_text(inputs, encoding="utf-8")
        self._write_manifest(run)
        self._emit(
            "stage",
            {
                "phase": "run_start",
                "run_id": run.run_id,
                "recipe": recipe_obj.name,
                "topic": topic,
                "stages": [{"name": s.name, "title": s.title} for s in recipe_obj.stages],
                "run_dir": str(run.run_dir),
                "server": run.server,
                "dataset": run.dataset,
                "stage": run.stage,
                "step_mode": run.step_mode,
            },
        )

        skip = set(skip_stages or [])
        stopped_early = False
        for index, stage in enumerate(recipe_obj.stages, start=1):
            if step_mode and not self.control.stopped:
                run.status = "paused"
                self._write_manifest(run)
                self.control.pause()
                self._emit(
                    "stage",
                    {
                        "phase": "step_paused",
                        "run_id": run.run_id,
                        "index": index,
                        "total": len(recipe_obj.stages),
                        "next_stage": stage.name,
                        "next_title": stage.title,
                    },
                )
                if not self.control.wait_if_paused():
                    run.status = "stopped"
                    break
                run.status = "running"
                self._emit(
                    "stage",
                    {"phase": "resumed", "run_id": run.run_id, "name": stage.name, "index": index},
                )
            if self.control.paused:
                run.status = "paused"
                self._write_manifest(run)
                self._emit("stage", {"phase": "paused", "run_id": run.run_id})
                if not self.control.wait_if_paused():
                    run.status = "stopped"
                    break
                run.status = "running"
                self._emit("stage", {"phase": "resumed", "run_id": run.run_id})
            if self.control.stopped:
                run.status = "stopped"
                break
            if stage.name in skip:
                run.stages.append(
                    StageResult(name=stage.name, title=stage.title, ok=True, skipped=True)
                )
                continue
            if stopped_early:
                break
            stage_result = self._run_stage(run, stage, index, len(recipe_obj.stages))
            run.stages.append(stage_result)
            self._write_manifest(run)
            if self.control.stopped:
                run.status = "stopped"
                break
            if until and stage.name == until:
                stopped_early = True
            if not stage_result.ok and self.settings.pipeline.stop_on_stage_error:
                run.status = "failed"
                run.error = f"阶段 {stage.name} 失败：{stage_result.error}"
                break

        if self.control.stopped:
            run.status = "stopped"
        if run.status == "running":
            executed = [s for s in run.stages if not s.skipped]
            if stopped_early:
                run.status = "partial"
            elif executed and not all(s.ok for s in executed):
                run.status = "failed" if run.error else "partial"
            else:
                run.status = "done"

        # 合并全流程 Markdown 报告
        run.finished_at = time.time()
        self._write_manifest(run)
        self._write_report(run, recipe_obj)
        self._write_state(run, recipe_obj)
        self._emit("stage", {"phase": "run_end", "run_id": run.run_id, "status": run.status})
        return run

    def _write_state(self, run: PipelineRun, recipe: Recipe) -> None:
        """写入项目级科研状态（借鉴 AI-research-SKILLs 的 autoresearch 工作区模型）。"""
        import datetime as _dt

        import yaml

        state = {
            "project": self.settings.project_name or self.project_dir.name,
            "topic": run.topic,
            "recipe": recipe.name,
            "run_id": run.run_id,
            "status": run.status,
            "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(run.run_dir),
            "mock": self.mock,
            "stages": [
                {
                    "name": item.name,
                    "title": item.title,
                    "ok": item.ok,
                    "skipped": item.skipped,
                    "artifacts": [Path(a).name for a in item.artifacts],
                }
                for item in run.stages
            ],
            "next_actions": self._next_actions(run),
        }
        try:
            (self.project_dir / "research-state.yaml").write_text(
                yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
        except OSError:
            pass

        # findings.md：把各阶段核心内容汇总成一份持续演进的叙事
        lines = [
            f"# Findings — {state['project']}",
            "",
            f"> 最近更新：{state['updated_at']} · recipe `{recipe.name}` · run `{run.run_id}`",
            "",
            f"## 研究主题\n\n{run.topic}\n",
        ]
        for item in run.stages:
            if not item.ok or not item.content.strip():
                continue
            body = item.content.strip()
            lines.append(f"## {item.title}\n")
            lines.append(body[:3000] + ("\n…（详见 run 目录）" if len(body) > 3000 else ""))
            lines.append("")
        lines.append("## 下一步\n")
        lines.extend(f"- {action}" for action in state["next_actions"])
        try:
            (self.project_dir / "findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

        # research-log.md：追加一行决策时间线
        try:
            log = self.project_dir / "research-log.md"
            if not log.exists():
                log.write_text("# Research Log\n\n| 时间 | Recipe | 状态 | 主题 | Run |\n|---|---|---|---|---|\n", encoding="utf-8")
            with log.open("a", encoding="utf-8") as fh:
                stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                fh.write(f"| {stamp} | {recipe.name} | {run.status} | {run.topic[:40]} | `{run.run_id}` |\n")
        except OSError:
            pass

    @staticmethod
    def _next_actions(run: PipelineRun) -> list[str]:
        failed = [item for item in run.stages if not item.ok and not item.skipped]
        if failed:
            return [f"修复阶段「{item.title}」：{item.error or '查看该阶段 output.md'}" for item in failed[:3]]
        mapping = {
            "survey": "基于文献空白确定 3 个候选方向（运行 `sciforge idea generate`）",
            "read": "把精读结论写入 notes/ 并对比方法差异",
            "ideate": "选择主攻方向，用 `sciforge pipeline run --recipe experiment` 进入实验",
            "design": "按实验矩阵实现代码（`sciforge exp scaffold`）",
            "code": "本地 smoke 通过后，用 `sciforge exp launch --server <name>` 上 GPU",
            "run": "训练完成后用 `sciforge exp results` 收集指标",
            "analyze": "把真实结果整理成 figures/ 数据，并检查论文叙事",
            "figures": "检查图在灰度/缩放下的可读性",
            "write": "补全引用并运行 `sciforge write compile`",
            "review": "按 Major/Minor 清单修复后重新自检",
        }
        done = {item.name for item in run.stages if item.ok}
        actions = [action for stage, action in mapping.items() if stage in done]
        return actions[-3:] or ["运行 `sciforge pipeline run --recipe full --topic ...` 开始完整流程"]

    def _write_report(self, run: PipelineRun, recipe: Recipe) -> None:
        lines = [
            "# SciForge 研究流水线报告\n",
            f"- Run ID：`{run.run_id}`",
            f"- Recipe：`{recipe.name}` — {recipe.title}",
            f"- 主题：{run.topic}",
            f"- 状态：{run.status}",
            f"- 耗时：{round((run.finished_at or time.time()) - run.started_at, 1)}s",
            "",
        ]
        if self.mock:
            lines.insert(4, "> 离线 Mock 模式：内容为流程演示，接入 LLM API 后重跑可获得真实产出。\n")
        for index, result in enumerate(run.stages, start=1):
            status = "✅" if result.ok else ("⏭" if result.skipped else "❌")
            lines.append(f"## {index}. {result.title} {status}\n")
            if result.content:
                lines.append(result.content)
                lines.append("")
            if result.artifacts:
                lines.append("产出：" + "、".join(Path(a).name for a in result.artifacts) + "\n")
        (run.run_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def list_runs(project_dir: str | Path) -> list[dict[str, Any]]:
    runs_dir = Path(project_dir).expanduser().resolve() / ".sciforge" / "runs"
    if not runs_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        manifest = run_dir / "run.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                items.append(data)
            except (json.JSONDecodeError, OSError):
                continue
    return items


def load_run(project_dir: str | Path, run_id: str) -> dict[str, Any] | None:
    manifest = Path(project_dir).expanduser().resolve() / ".sciforge" / "runs" / run_id / "run.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


__all__ = [
    "Stage",
    "Recipe",
    "StageResult",
    "PipelineRun",
    "PipelineRunner",
    "RECIPES",
    "get_recipe",
    "list_runs",
    "load_run",
]
