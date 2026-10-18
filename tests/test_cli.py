"""CLI 端到端测试。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sciforge.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "SciForge" in result.output


def test_init_creates_project(sciforge_home: Path, tmp_path: Path) -> None:
    proj = tmp_path / "new-research"
    result = runner.invoke(app, ["init", str(proj), "--name", "我的课题"])
    assert result.exit_code == 0, result.output
    for sub in ("notes", "experiments", "results", "figures", "paper", ".sciforge"):
        assert (proj / sub).exists()
    assert "我的课题" in (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert (sciforge_home / "config.toml").exists()


def test_tools_list(sciforge_home: Path) -> None:
    result = runner.invoke(app, ["tools", "list"])
    assert result.exit_code == 0, result.output
    assert "read_file" in result.output


def test_config_path(sciforge_home: Path) -> None:
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert "sciforge-home" in result.output.replace("\n", "")


def test_doctor_json(sciforge_home: Path) -> None:
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output[result.output.find("{") :])
    assert "Python" in data


def test_pipeline_run_mock(sciforge_home: Path, project: Path) -> None:
    result = runner.invoke(
        app,
        ["pipeline", "run", "--recipe", "survey", "--topic", "测试主题", "--mock", "--project", str(project)],
    )
    assert result.exit_code == 0, result.output
    assert "Pipeline" in result.output


def test_demo_offline(sciforge_home: Path, tmp_path: Path) -> None:
    dest = tmp_path / "demo-project"
    result = runner.invoke(app, ["demo", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "DEMO.md").exists()
    assert (dest / "notes" / "topic.md").exists()
    assert list((dest / ".sciforge" / "runs").glob("*/REPORT.md"))
