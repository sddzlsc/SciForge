"""Pipeline 引擎测试（离线 Mock）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.config import get_settings
from sciforge.pipeline.engine import PipelineRunner, list_runs, load_run
from sciforge.pipeline.recipes import RECIPES, get_recipe


def test_recipes_available() -> None:
    assert {"full", "survey", "experiment", "paper", "daily", "figure"} <= set(RECIPES)
    assert get_recipe("full") is not None
    assert get_recipe("nope") is None


def test_mock_survey_pipeline(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    runner = PipelineRunner(settings, project, mock=True)
    run = runner.run("survey", "扩散模型加速")
    assert run.status == "done"
    assert [stage.name for stage in run.stages] == ["survey", "read", "ideate"]
    assert all(stage.ok for stage in run.stages)
    assert (run.run_dir / "REPORT.md").exists()
    report = (run.run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "扩散模型加速" in report
    assert "离线 Mock 模式" in report
    # 每个阶段都有独立输出
    for stage_dir in run.run_dir.glob("*"):
        if stage_dir.is_dir():
            assert (stage_dir / "output.md").exists()


def test_pipeline_until_partial(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    run = PipelineRunner(settings, project, mock=True).run("survey", "主题", until="read")
    assert run.status == "partial"
    assert [stage.name for stage in run.stages] == ["survey", "read"]


def test_pipeline_manifest_and_history(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    run = PipelineRunner(settings, project, mock=True).run("survey", "主题")
    runs = list_runs(project)
    assert any(item["run_id"] == run.run_id for item in runs)
    loaded = load_run(project, run.run_id)
    assert loaded and loaded["status"] == "done"
    assert loaded["stages"][0]["name"] == "survey"


def test_unknown_recipe_raises(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    with pytest.raises(ValueError):
        PipelineRunner(settings, project, mock=True).run("nope", "x")


def test_research_state_files(sciforge_home: Path, project: Path) -> None:
    """流水线结束必须写项目级科研状态（对齐 autoresearch 工作区模型）。"""
    import yaml

    settings = get_settings(project)
    run = PipelineRunner(settings, project, mock=True).run("survey", "状态文件测试")
    state_path = project / "research-state.yaml"
    findings = project / "findings.md"
    log = project / "research-log.md"
    assert state_path.exists() and findings.exists() and log.exists()
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert state["run_id"] == run.run_id
    assert state["status"] == "done"
    assert [s["name"] for s in state["stages"]] == ["survey", "read", "ideate"]
    assert state["next_actions"]
    assert "状态文件测试" in findings.read_text(encoding="utf-8")
    assert run.run_id in log.read_text(encoding="utf-8")
