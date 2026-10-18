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


def test_full_recipe_order_matches_research_flow() -> None:
    recipe = get_recipe("full")
    assert recipe is not None
    assert [stage.name for stage in recipe.stages] == [
        "scope",
        "survey",
        "read",
        "ideate",
        "gpu",
        "data",
        "design",
        "code",
        "run",
        "analyze",
        "figures",
        "write",
        "review",
    ]
    prompts = Path("sciforge/prompts/stages")
    for stage in recipe.stages:
        assert (prompts / f"{stage.prompt}_zh.md").is_file(), f"缺少阶段 prompt：{stage.prompt}"


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


def test_pipeline_records_server_and_dataset(sciforge_home: Path, project: Path) -> None:
    from sciforge.pipeline.engine import PipelineRunner

    settings = get_settings(project)
    runner = PipelineRunner(settings, project, mock=True)
    run = runner.run("figure", "测试主题", server="gpu1", dataset="./data")
    assert run.server == "gpu1"
    assert run.dataset == "./data"
    data = run.to_dict()
    assert data["server"] == "gpu1" and data["dataset"] == "./data"
    manifest = (run.run_dir / "run.json").read_text(encoding="utf-8")
    assert "gpu1" in manifest and "./data" in manifest


def test_run_control_pause_resume_stop() -> None:
    import threading
    import time as _time

    from sciforge.pipeline.control import RunControl

    control = RunControl()
    assert control.wait_if_paused() is True
    assert control.pause() is True
    assert control.paused is True
    thread = threading.Thread(target=lambda: (_time.sleep(0.1), control.resume()))
    thread.start()
    assert control.wait_if_paused() is True
    thread.join()
    assert control.paused is False
    control.stop()
    assert control.wait_if_paused() is False


def test_pipeline_runner_control_methods(sciforge_home: Path, project: Path) -> None:
    from sciforge.pipeline.engine import PipelineRunner

    runner = PipelineRunner(get_settings(project), project, mock=True)
    assert runner.pause() is True
    assert runner.control.paused is True
    runner.resume()
    assert runner.control.paused is False
    runner.stop()
    assert runner.control.stopped is True


def test_pipeline_pause_between_stages(sciforge_home: Path, project: Path, monkeypatch) -> None:
    import threading
    import time as _time

    from sciforge.pipeline.engine import PipelineRunner

    runner = PipelineRunner(get_settings(project), project, mock=True)
    original = runner._run_stage

    def slow_stage(*args, **kwargs):  # noqa: ANN002, ANN003
        _time.sleep(0.12)
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_stage", slow_stage)
    events: list = []
    runner.bus.subscribe(lambda event: events.append(event))
    box: dict = {}
    thread = threading.Thread(target=lambda: box.setdefault("run", runner.run("paper", "pause test")))
    thread.start()

    deadline = _time.time() + 8
    while _time.time() < deadline:
        if any(
            event.kind == "stage" and getattr(event, "payload", {}).get("phase") == "end"
            for event in list(events)
        ):
            break
        _time.sleep(0.02)

    runner.pause()
    _time.sleep(0.25)
    assert runner.control.paused is True
    assert thread.is_alive(), "暂停后流水线不应继续跑完"

    runner.resume()
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert box["run"].status == "done"
