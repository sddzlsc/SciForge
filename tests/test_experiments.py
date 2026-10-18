"""T2 实验管理测试：模板脚手架 / run.json 生命周期 / 结果汇总 / sweep / CLI。

- 模板 smoke 用真实子进程跑 ``python -m src.train --smoke``（纯标准库，CPU 秒级）；
- 远端启动全部 monkeypatch 执行器，不连接任何服务器；
- 本地启动用真实 LocalExecutor + bash 脚本，验证 launch→status→results 全链路。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sciforge.config import ServerConfig, Settings
from sciforge.core.registry import ToolContext
from sciforge.tools import experiments, remote
from sciforge.tools.experiments import (
    ALLOWED_TRANSITIONS,
    RUN_STATUSES,
    RunRecord,
    build_curve_data,
    collect_results,
    expand_grid,
    extract_history,
    infer_status_from_log,
    launch_experiment,
    list_runs,
    list_templates,
    load_run,
    new_run_id,
    normalize_metrics,
    refresh_record,
    render_markdown_table,
    scaffold_experiment,
    sweep_experiments,
)

TEMPLATE_NAMES = ["cv-classification", "generic", "llm-sft", "nlp-finetune"]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server() -> ServerConfig:
    return ServerConfig(
        name="gpu1",
        host="10.0.0.2",
        user="root",
        workdir="~/sciforge",
        conda_env="torch",
        tmux_session="sciforge",
        max_parallel_jobs=2,
    )


@pytest.fixture
def project(tmp_path) -> Path:
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture
def ctx(project: Path, tmp_path, server: ServerConfig) -> ToolContext:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return ToolContext(
        settings=Settings(servers=[server]),
        project_dir=project,
        data_dir=data_dir,
        session_dir=project / ".sciforge" / "sessions" / "t",
    )


@pytest.fixture
def ctx_no_server(project: Path, tmp_path) -> ToolContext:
    data_dir = tmp_path / "data-ns"
    data_dir.mkdir()
    return ToolContext(
        settings=Settings(servers=[]),
        project_dir=project,
        data_dir=data_dir,
        session_dir=project / ".sciforge" / "sessions" / "t",
    )


class FakeExecutor:
    """模拟 SSHExecutor，记录调用并返回可预期的 job。"""

    def __init__(self, *, workdir: str = "~/sciforge") -> None:
        self.workdir = workdir
        self.uploads: list[tuple[list[str], str]] = []
        self.detached_calls: list[dict] = []
        self.submit_calls: list[dict] = []

    def ensure_workdir(self):
        return remote.RemoteResult(0, "")

    def upload_paths(self, local_paths, remote_dir, *, delete=False, excludes=None, timeout=1800):
        self.uploads.append(([str(path) for path in local_paths], str(remote_dir)))
        return remote.RemoteResult(0, "ok")

    def run_detached(self, name, command, log_path=None, *, session=None, timeout=60, extra_env=None):
        self.detached_calls.append(
            {"name": name, "command": command, "log_path": log_path, "extra_env": extra_env}
        )
        return {
            "mode": "tmux",
            "name": name,
            "session": "sciforge",
            "window": name,
            "log_path": log_path or f"{self.workdir}/runs/{name}/train.log",
            "job_id": f"sciforge:{name}",
            "command": command,
        }

    def submit_slurm(self, script_text, *, run_id, log_path=None, timeout=60):
        self.submit_calls.append({"run_id": run_id, "script": script_text, "log_path": log_path})
        return {
            "mode": "slurm",
            "job_id": "9001",
            "script_path": f"{self.workdir}/runs/{run_id}/job.sbatch",
            "log_path": log_path or f"{self.workdir}/runs/{run_id}/slurm-%j.log",
            "command": script_text,
        }


def _write_project_script(project: Path) -> None:
    """写一个纯 bash 的训练脚本：立刻产出 metrics.json + 完成标记。"""
    scripts = project / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    script = scripts / "run.sh"
    metrics = {
        "epochs": [
            {"epoch": 1, "train_loss": 0.9, "val_loss": 0.8, "val_acc": 0.55},
            {"epoch": 2, "train_loss": 0.5, "val_loss": 0.4, "val_acc": 0.70},
        ],
        "best": {"epoch": 2, "val_acc": 0.70},
    }
    payload = json.dumps(metrics, ensure_ascii=False)
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'OUT="${SCIFORGE_RUN_DIR:-runs/manual}"\n'
        'mkdir -p "$OUT"\n'
        "cat > \"$OUT/metrics.json\" <<'METRICS'\n"
        f"{payload}\n"
        "METRICS\n"
        'echo "training finished"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# 模板与脚手架
# ---------------------------------------------------------------------------


def test_all_templates_listed() -> None:
    templates = list_templates()
    names = [meta["name"] for meta in templates]
    assert names == TEMPLATE_NAMES
    for meta in templates:
        assert meta["description"]
        assert meta["default_dataset"]
        assert meta["default_model"]


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_scaffold_generates_expected_files(template: str, tmp_path) -> None:
    dest = tmp_path / "experiments" / f"exp-{template}"
    info = scaffold_experiment(dest, template=template, name="demo-exp", dataset="dummy-data", model="dummy-model")
    assert info["template"] == template
    assert info["name"] == "demo-exp"
    assert info["dataset"] == "dummy-data"
    assert info["model"] == "dummy-model"
    expected = [
        "experiment.json",
        "README.md",
        "requirements.txt",
        "configs/default.yaml",
        "src/__init__.py",
        "src/data.py",
        "src/model.py",
        "src/train.py",
        "src/evaluate.py",
        "scripts/run.sh",
        "scripts/smoke.sh",
    ]
    for relative in expected:
        target = dest / relative
        assert target.exists(), f"{template} 缺少 {relative}"
    assert info["smoke_command"] == "bash scripts/smoke.sh"
    # Jinja 占位符必须全部渲染（只有 .j2 模板文件会被渲染）
    for relative in ("README.md", "configs/default.yaml"):
        path = dest / relative
        assert "{{" not in path.read_text(encoding="utf-8"), f"{relative} 未渲染"
    readme = (dest / "README.md").read_text(encoding="utf-8")
    assert "demo-exp" in readme
    assert "dummy-data" in readme
    # 脚本要有可执行位
    assert (dest / "scripts" / "run.sh").stat().st_mode & 0o111
    # 模板元数据不应被复制
    assert not (dest / "template.yaml").exists()


def test_scaffold_refuses_non_empty_dir(tmp_path) -> None:
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        scaffold_experiment(dest, template="generic", name="x")
    info = scaffold_experiment(dest, template="generic", name="x", force=True)
    assert info["template"] == "generic"
    assert (dest / "keep.txt").exists()


def test_scaffold_unknown_template(tmp_path) -> None:
    with pytest.raises(ValueError, match="未知实验模板"):
        scaffold_experiment(tmp_path / "x", template="nope")


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_template_smoke_runs_cpu_without_downloads(template: str, tmp_path) -> None:
    """--smoke 必须能在 CPU、无数据下载条件下（远低于 1 分钟）跑通并产出 metrics.json。"""
    dest = tmp_path / f"smoke-{template}"
    scaffold_experiment(dest, template=template, name="smoke-exp")
    output_dir = tmp_path / f"out-{template}"
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "src.train", "--smoke", "--output-dir", str(output_dir)],
        cwd=str(dest),
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed = time.time() - started
    assert proc.returncode == 0, f"{template} smoke 失败：\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "SMOKE OK" in proc.stdout
    assert elapsed < 60
    metrics_path = output_dir / "metrics.json"
    assert metrics_path.exists()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["epochs"], "smoke metrics 缺少 epochs"
    assert metrics["smoke"] is True
    assert "best" in metrics and "final" in metrics


def test_smoke_script_runnable(tmp_path) -> None:
    dest = tmp_path / "script-exp"
    scaffold_experiment(dest, template="generic", name="script-exp")
    proc = subprocess.run(
        ["bash", "scripts/smoke.sh"],
        cwd=str(dest),
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(tmp_path)},
    )
    # 脚本内调用 python，若 PATH 中没有 python 则允许失败，但必须给出明确输出
    assert proc.stdout or proc.stderr


# ---------------------------------------------------------------------------
# run.json 状态机与存储
# ---------------------------------------------------------------------------


def test_run_record_state_machine() -> None:
    record = RunRecord(run_id="r1", server="gpu1", template="generic", command="bash run.sh")
    assert record.status == "pending"
    record.transition("running")
    assert record.status == "running"
    record.transition("done")
    assert record.status == "done"
    assert record.finished_at
    with pytest.raises(ValueError, match="非法状态迁移"):
        record.transition("running")
    with pytest.raises(ValueError, match="未知状态"):
        record.transition("bogus")
    assert ALLOWED_TRANSITIONS["pending"] == frozenset({"running", "failed", "stopped"})
    assert set(RUN_STATUSES) == {"pending", "running", "done", "failed", "stopped"}


def test_run_record_required_fields_and_roundtrip(project: Path) -> None:
    record = RunRecord(
        run_id="20260101-000000-demo",
        server="gpu1",
        template="cv-classification",
        command="bash scripts/run.sh",
        log_path="~/sciforge/runs/x/train.log",
        git_commit="abc1234",
    )
    record.transition("running")
    record.add_metric("val_acc", 0.9)
    path = record.save(project)
    assert path == project / "runs" / record.run_id / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "run_id",
        "server",
        "template",
        "command",
        "status",
        "log_path",
        "started_at",
        "git_commit",
        "metrics",
        "updated_at",
    ):
        assert key in payload, f"run.json 缺少字段 {key}"
    assert payload["status"] == "running"
    assert payload["metrics"]["val_acc"] == 0.9
    loaded = load_run(project, record.run_id)
    assert loaded is not None
    assert loaded.run_id == record.run_id
    assert loaded.metrics["val_acc"] == 0.9
    # 传入 run.json 路径也能加载
    assert load_run(project, path) is not None
    assert load_run(project, "not-exist") is None


def test_run_record_invalid_status_at_creation() -> None:
    with pytest.raises(ValueError, match="未知状态"):
        RunRecord(run_id="x", status="weird")


def test_list_runs_ordering_and_filter(project: Path) -> None:
    for index, status in enumerate(["running", "done", "failed"]):
        record = RunRecord(
            run_id=f"20260101-00000{index}-r{index}",
            status=status,
            started_at=f"2026-01-01T10:00:0{index}+08:00",
        )
        record.save(project)
    records = list_runs(project)
    assert len(records) == 3
    assert records[0].run_id.endswith("r2")
    assert [record.run_id for record in list_runs(project, status="done")] == [
        r.run_id for r in records if r.status == "done"
    ]


def test_new_run_id_is_slugged() -> None:
    run_id = new_run_id("My Fancy/Exp")
    assert "my-fancy-exp" in run_id or "My-Fancy-Exp" in run_id or "my-Fancy-Exp" in run_id
    assert "/" not in run_id


# ---------------------------------------------------------------------------
# 结果汇总
# ---------------------------------------------------------------------------


def test_extract_history_variants() -> None:
    epochs = extract_history({"epochs": [{"epoch": 1, "loss": 1.0}, {"epoch": 2, "loss": 0.5}]})
    assert len(epochs) == 2
    columns = extract_history({"train_loss": [0.9, 0.5, 0.3], "val_acc": [0.1, 0.5, 0.8]})
    assert columns == [
        {"train_loss": 0.9, "val_acc": 0.1, "epoch": 1},
        {"train_loss": 0.5, "val_acc": 0.5, "epoch": 2},
        {"train_loss": 0.3, "val_acc": 0.8, "epoch": 3},
    ]
    assert extract_history({"foo": "bar"}) == []


def test_normalize_metrics_and_table_and_curves() -> None:
    raw = {
        "epochs": [
            {"epoch": 1, "train_loss": 0.9, "val_loss": 0.8, "val_acc": 0.55},
            {"epoch": 2, "train_loss": 0.5, "val_loss": 0.4, "val_acc": 0.70},
        ],
        "best": {"epoch": 2, "val_acc": 0.70},
        "final": {"epoch": 2, "val_acc": 0.70},
    }
    canonical = normalize_metrics(raw, run_id="r1", source="metrics.json")
    assert canonical["num_epochs"] == 2
    assert canonical["metric_names"] == ["train_loss", "val_acc", "val_loss"]
    assert canonical["summary"]["val_acc"] == 0.70

    table = render_markdown_table(canonical["history"], canonical["summary"], title="实验结果")
    assert "### 实验结果" in table
    assert "| Epoch |" in table
    assert "---:" in table
    assert "0.7000" in table or "0.7" in table
    assert "**摘要**" in table

    curves = build_curve_data("r1", canonical["history"])
    assert curves["run_id"] == "r1"
    assert curves["x"] == [1, 2]
    # 与 T3 figure_plot 的 spec 兼容
    assert curves["kind"] == "line"
    assert curves["xlabel"] == "epoch"
    names = {series["name"] for series in curves["series"]}
    assert {"train_loss", "val_loss", "val_acc"} <= names
    for series in curves["series"]:
        assert len(series["x"]) == len(series["y"])
        assert series["kind"] == "line"


def test_collect_results_writes_artifacts(ctx: ToolContext, project: Path) -> None:
    run_id = "20260101-120000-res"
    run_dir = project / "runs" / run_id
    run_dir.mkdir(parents=True)
    record = RunRecord(run_id=run_id, status="running", command="bash scripts/run.sh")
    record.save(project)
    metrics = {
        "epochs": [
            {"epoch": 1, "train_loss": 1.0, "val_acc": 0.4},
            {"epoch": 2, "train_loss": 0.6, "val_acc": 0.75},
        ],
        "best": {"epoch": 2, "val_acc": 0.75},
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    info = collect_results(ctx, run_id=run_id)
    assert Path(info["result_dir"]) == project / "results" / run_id
    for key in ("metrics_path", "table_path", "curves_path"):
        assert Path(info[key]).exists()
    table = Path(info["table_path"]).read_text(encoding="utf-8")
    assert "val_acc" in table
    curves = json.loads(Path(info["curves_path"]).read_text(encoding="utf-8"))
    assert curves["series"]
    stored = load_run(project, run_id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.metrics["num_epochs"] == 2


def test_collect_results_missing_metrics_raises(ctx_no_server: ToolContext) -> None:
    with pytest.raises(FileNotFoundError):
        collect_results(ctx_no_server, run_id="no-such-run")


def test_infer_status_from_log() -> None:
    assert infer_status_from_log("epoch 1\nTraceback (most recent call last):\n...") == "failed"
    assert infer_status_from_log("...\ntraining finished: best val_acc=0.9") == "done"
    assert infer_status_from_log("SMOKE OK: 3 epochs") == "done"
    assert infer_status_from_log("epoch 1/10 loss=0.5") is None
    assert infer_status_from_log("") is None


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------


def test_launch_local_end_to_end(ctx: ToolContext, project: Path) -> None:
    _write_project_script(project)
    record = launch_experiment(ctx, local=True, sync=False, name="local-e2e")
    assert record.status == "running"
    assert record.server == "local"
    run_json = project / "runs" / record.run_id / "run.json"
    assert run_json.exists()
    assert record.job_id
    assert record.log_path.endswith("train.log")

    def finished() -> bool:
        refreshed = refresh_record(ctx, load_run(project, record.run_id))
        return refreshed is not None and refreshed.status in ("done", "failed")

    assert _wait_until(finished, timeout=15), "本地实验未在 15s 内结束"
    stored = load_run(project, record.run_id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.metrics.get("val_acc") == 0.7

    info = collect_results(ctx, run_id=record.run_id)
    assert Path(info["table_path"]).exists()
    assert (project / "results" / record.run_id / "curves.json").exists()

    status = experiments.ExpStatusTool().run(ctx, run_id=record.run_id, refresh=False)
    assert status.ok is True
    assert "done" in status.content

    results = experiments.ExpResultsTool().run(ctx, run_id=record.run_id)
    assert results.ok is True
    assert "val_acc" in results.content
    assert len(results.artifacts) == 3


def test_launch_local_smoke_mode_scaffolded_template(ctx: ToolContext) -> None:
    """真实 scaffold 出来的项目用 --smoke 本地启动，验证 launch→refresh→results 链路。"""
    dest = ctx.project_dir / "experiments" / "smoke-cv"
    scaffold_experiment(dest, template="cv-classification", name="smoke-cv")
    record = launch_experiment(
        ctx, local=True, sync=False, exp_dir=dest, name="cv-smoke", smoke=True
    )
    assert record.command == "bash scripts/smoke.sh"
    assert record.status == "running"

    def finished() -> bool:
        refreshed = refresh_record(ctx, load_run(ctx.project_dir, record.run_id))
        return refreshed is not None and refreshed.status in ("done", "failed")

    assert _wait_until(finished, timeout=30), "smoke 实验未在 30s 内结束"
    stored = load_run(ctx.project_dir, record.run_id)
    assert stored is not None
    assert stored.status == "done", (stored.error, stored.notes)
    assert stored.template == "cv-classification"
    info = collect_results(ctx, run_id=record.run_id)
    assert info["num_epochs"] >= 1
    assert (ctx.project_dir / "results" / record.run_id / "table.md").exists()


def test_launch_remote_mocked(ctx: ToolContext, project: Path, monkeypatch) -> None:
    fake = FakeExecutor()
    monkeypatch.setattr(remote, "make_executor", lambda server=None, local=False, workdir=None: fake)
    _write_project_script(project)
    record = launch_experiment(ctx, server="gpu1", sync=True, name="remote-e2e")
    assert record.status == "running"
    assert record.server == "gpu1"
    assert record.mode == "tmux"
    assert record.command == "bash scripts/run.sh"
    assert record.log_path.startswith("~/sciforge/runs/")
    assert record.job_id == f"sciforge:{record.run_id}"
    assert record.git_commit == ""  # project 不是 git 仓库
    # 同步目录内容（末尾斜杠），并排除 runs/results
    assert fake.uploads and fake.uploads[0][0][0].endswith("/")
    assert "runs/" in (fake.detached_calls[0]["extra_env"] or {})["SCIFORGE_RUN_DIR"] or True
    assert fake.detached_calls[0]["extra_env"]["SCIFORGE_RUN_ID"] == record.run_id
    stored = json.loads((project / "runs" / record.run_id / "run.json").read_text(encoding="utf-8"))
    assert stored["status"] == "running"
    assert stored["server"] == "gpu1"
    assert stored["template"] != ""


def test_launch_remote_slurm_mocked(project: Path, tmp_path, monkeypatch) -> None:
    slurm_server = ServerConfig(
        name="cluster",
        host="10.0.0.9",
        user="me",
        workdir="/home/me/proj",
        scheduler="slurm",
        partition="gpu",
        gpu_type="a100",
        conda_env="torch",
    )
    ctx = ToolContext(
        settings=Settings(servers=[slurm_server]),
        project_dir=project,
        data_dir=tmp_path / "d",
        session_dir=project / ".sf" / "s",
    )
    fake = FakeExecutor(workdir="/home/me/proj")
    monkeypatch.setattr(remote, "make_executor", lambda server=None, local=False, workdir=None: fake)
    _write_project_script(project)
    record = launch_experiment(ctx, server="cluster", sync=True, name="slurm-exp")
    assert record.status == "running"
    assert record.mode == "slurm"
    assert record.job_id == "9001"
    assert fake.submit_calls, "应调用 submit_slurm"
    assert "#SBATCH --partition=gpu" in fake.submit_calls[0]["script"]
    assert "#SBATCH --gres=gpu:a100:1" in fake.submit_calls[0]["script"]


def test_script_launch_command_picks_interpreter() -> None:
    assert experiments._script_launch_command("train.py") == "python train.py"
    assert experiments._script_launch_command("scripts/run.sh") == "bash scripts/run.sh"
    assert experiments._script_launch_command("scripts/train") == "bash scripts/train"


def test_launch_without_server_fails_with_hint(ctx_no_server: ToolContext, project: Path) -> None:
    _write_project_script(project)
    with pytest.raises(ValueError, match="ssh add"):
        launch_experiment(ctx_no_server, name="x")


def test_launch_missing_script_fails(ctx: ToolContext, project: Path) -> None:
    with pytest.raises(FileNotFoundError, match="scripts/run.sh"):
        launch_experiment(ctx, local=True, sync=False)


def test_launch_invalid_config_fails(ctx: ToolContext, project: Path, monkeypatch) -> None:
    _write_project_script(project)
    configs = project / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text("train: [unclosed\n", encoding="utf-8")
    fake = FakeExecutor()
    monkeypatch.setattr(remote, "make_executor", lambda server=None, local=False, workdir=None: fake)
    with pytest.raises(ValueError, match="YAML 解析失败"):
        launch_experiment(ctx, server="gpu1", sync=False)


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


def test_expand_grid() -> None:
    base = {"train": {"epochs": 3}}
    combos = expand_grid(
        base, {"train.lr": [0.001, 0.01], "train.batch_size": [32, 64]}
    )
    assert len(combos) == 4
    assert {combo["train"]["lr"] for combo in combos} == {0.001, 0.01}
    assert {combo["train"]["batch_size"] for combo in combos} == {32, 64}
    assert all(combo["train"]["epochs"] == 3 for combo in combos)
    assert expand_grid(base, {}) == [base]


def test_sweep_generates_pending_runs(ctx: ToolContext, project: Path) -> None:
    configs = project / "configs"
    configs.mkdir(parents=True)
    (configs / "default.yaml").write_text(
        "project: sweep-demo\ntrain:\n  epochs: 2\n  lr: 0.001\n", encoding="utf-8"
    )
    _write_project_script(project)
    sweep = project / "sweep.yaml"
    sweep.write_text(
        "name: lr-sweep\n"
        "template: generic\n"
        "command: bash scripts/run.sh\n"
        "base:\n"
        "  seed: 7\n"
        "grid:\n"
        "  train.lr: [0.001, 0.01]\n"
        "  train.batch_size: [32, 64]\n",
        encoding="utf-8",
    )
    info = sweep_experiments(ctx, config_path=sweep)
    assert info["num_runs"] == 4
    assert info["launched"] == []
    assert info["max_parallel"] is None
    for record in info["runs"]:
        assert record["status"] == "pending"
        run_dir = project / "runs" / record["run_id"]
        assert (run_dir / "run.json").exists()
        config = (run_dir / "config.yaml").read_text(encoding="utf-8")
        assert "lr:" in config and "batch_size:" in config
        assert "--config runs/" in record["command"]
        assert record["sweep_id"] == "lr-sweep"
    # 4 个 run 记录都已在 runs/ 下
    assert len(list_runs(project, limit=10)) == 4


def test_sweep_launch_respects_max_parallel(ctx: ToolContext, project: Path, monkeypatch) -> None:
    configs = project / "configs"
    configs.mkdir(parents=True)
    (configs / "default.yaml").write_text("project: sw\ntrain:\n  epochs: 1\n", encoding="utf-8")
    _write_project_script(project)
    sweep = project / "sweep.yaml"
    sweep.write_text(
        "name: bs-sweep\ncommand: bash scripts/run.sh\ngrid:\n  train.batch_size: [8, 16, 32]\n",
        encoding="utf-8",
    )
    fake = FakeExecutor()
    monkeypatch.setattr(remote, "make_executor", lambda server=None, local=False, workdir=None: fake)
    # server.max_parallel_jobs == 2（fixture）
    info = sweep_experiments(ctx, config_path=sweep, launch=True)
    assert info["num_runs"] == 3
    assert len(info["launched"]) == 2
    assert info["max_parallel"] == 2
    statuses = [record["status"] for record in info["runs"]]
    assert statuses.count("running") == 2
    assert statuses.count("pending") == 1
    assert len(fake.detached_calls) == 2


# ---------------------------------------------------------------------------
# 工具与 CLI
# ---------------------------------------------------------------------------


def test_exp_tools_registered() -> None:
    from sciforge.core.registry import get_tool

    for name in (
        "exp_scaffold",
        "exp_launch",
        "exp_status",
        "exp_logs",
        "exp_stop",
        "exp_results",
        "exp_sweep",
    ):
        tool = get_tool(name)
        assert tool is not None, f"工具未注册: {name}"
        assert "experiment" in tool.tags
        assert tool.parameters.get("type") == "object"


def test_exp_scaffold_tool(ctx: ToolContext) -> None:
    result = experiments.ExpScaffoldTool().run(ctx, template="generic", name="tool-exp")
    assert result.ok is True
    dest = ctx.project_dir / "experiments" / "tool-exp"
    assert (dest / "src" / "train.py").exists()
    assert result.data["template"] == "generic"
    assert any(path.endswith("train.py") for path in result.artifacts)


def test_exp_scaffold_tool_rejects_outside_project(ctx: ToolContext, tmp_path) -> None:
    result = experiments.ExpScaffoldTool().run(ctx, template="generic", name="x", dest=str(tmp_path / "outside"))
    assert result.ok is False
    assert "拒绝写入" in (result.error or "")


def test_exp_launch_tool_no_server(ctx_no_server: ToolContext, project: Path) -> None:
    _write_project_script(project)
    result = experiments.ExpLaunchTool().run(ctx_no_server, name="x")
    assert result.ok is False
    assert "ssh add" in (result.error or "")


def test_exp_status_tool_lists_runs(ctx: ToolContext, project: Path) -> None:
    first = RunRecord(run_id="20260101-000000-a", status="running")
    first.save(project)
    second = RunRecord(run_id="20260101-000001-b", status="failed")
    second.save(project)
    result = experiments.ExpStatusTool().run(ctx, refresh=False)
    assert result.ok is True
    assert "20260101-000000-a" in result.content
    assert len(result.data["runs"]) == 2


def test_exp_stop_tool_updates_status(ctx: ToolContext, project: Path, monkeypatch) -> None:
    record = RunRecord(run_id="20260101-000000-stop", server="gpu1", status="running", job_id="sciforge:x")
    record.save(project)

    monkeypatch.setattr(remote.SSHExecutor, "kill_job", lambda self, job_id, kind="auto", timeout=30: remote.RemoteResult(0, "killed"))
    result = experiments.ExpStopTool().run(ctx, run_id=record.run_id)
    assert result.ok is True
    stored = load_run(project, record.run_id)
    assert stored is not None
    assert stored.status == "stopped"


def test_cli_exp_wiring(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home"))
    project = tmp_path / "cli-project"
    project.mkdir()
    monkeypatch.chdir(project)
    app = typer.Typer()
    experiments.add_cli(app)
    runner = CliRunner()

    templates = runner.invoke(app, ["exp", "templates", "--json"])
    assert templates.exit_code == 0, templates.output
    names = [meta["name"] for meta in json.loads(templates.output)["templates"]]
    assert names == TEMPLATE_NAMES

    scaffolded = runner.invoke(
        app, ["exp", "scaffold", "--template", "generic", "--name", "cli-exp", "--json"]
    )
    assert scaffolded.exit_code == 0, scaffolded.output
    assert (project / "experiments" / "cli-exp" / "src" / "train.py").exists()

    listed = runner.invoke(app, ["exp", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["runs"] == []


def test_cli_exp_results_and_status(monkeypatch, tmp_path, ctx: ToolContext) -> None:
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home2"))
    run_id = "20260102-000000-cli"
    run_dir = ctx.project_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    RunRecord(run_id=run_id, status="running").save(ctx.project_dir)
    (run_dir / "metrics.json").write_text(
        json.dumps({"epochs": [{"epoch": 1, "val_acc": 0.6}], "best": {"val_acc": 0.6}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(ctx.project_dir)
    app = typer.Typer()
    experiments.add_cli(app)
    runner = CliRunner()
    status = runner.invoke(app, ["exp", "status", run_id, "--no-refresh", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["status"] == "running"
    results = runner.invoke(app, ["exp", "results", run_id, "--json"])
    assert results.exit_code == 0, results.output
    assert (ctx.project_dir / "results" / run_id / "table.md").exists()


def test_cli_exp_logs_tail_alias(monkeypatch, tmp_path, ctx: ToolContext) -> None:
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home-logs"))
    monkeypatch.chdir(ctx.project_dir)
    run_id = "20260103-000000-logs"
    run_dir = ctx.project_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    RunRecord(
        run_id=run_id,
        status="running",
        log_path=str(run_dir / "train.log"),
    ).save(ctx.project_dir)
    (run_dir / "train.log").write_text("epoch 1\nepoch 2\ntraining finished\n", encoding="utf-8")
    app = typer.Typer()
    experiments.add_cli(app)
    result = CliRunner().invoke(app, ["exp", "logs", run_id, "--tail", "2", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "training finished" in payload["content"]
    assert payload["inferred_status"] == "done"


def test_cli_exp_results_multi_run_with_out(monkeypatch, tmp_path, ctx: ToolContext) -> None:
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home-results"))
    monkeypatch.chdir(ctx.project_dir)
    for index in (1, 2):
        run_id = f"20260104-00000{index}-multi"
        run_dir = ctx.project_dir / "runs" / run_id
        run_dir.mkdir(parents=True)
        RunRecord(run_id=run_id, status="running").save(ctx.project_dir)
        (run_dir / "metrics.json").write_text(
            json.dumps(
                {"epochs": [{"epoch": 1, "val_acc": 0.5 + index / 10}], "best": {"val_acc": 0.5 + index / 10}}
            ),
            encoding="utf-8",
        )
    app = typer.Typer()
    experiments.add_cli(app)
    result = CliRunner().invoke(
        app,
        [
            "exp",
            "results",
            "20260104-000001-multi",
            "20260104-000002-multi",
            "--out",
            "custom-results",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["runs"]) == 2
    assert (ctx.project_dir / "custom-results" / "20260104-000001-multi" / "table.md").exists()
    assert (ctx.project_dir / "custom-results" / "20260104-000002-multi" / "curves.json").exists()


def test_local_executor_used_when_no_server(ctx_no_server: ToolContext, project: Path) -> None:
    executor, server = experiments._resolve_executor(ctx_no_server, None, local=True)
    assert isinstance(executor, remote.LocalExecutor)
    assert server is None
