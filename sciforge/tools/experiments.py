"""实验管理：脚手架 / 启动 / 状态 / 日志 / 停止 / 结果汇总 / 参数 sweep。

核心数据结构是 ``<project>/runs/<run_id>/run.json``：

- 状态机：``pending → running → done | failed | stopped``；
- 必备字段：run_id / server / template / command / status / log_path /
  started_at / git_commit / metrics / updated_at；
- ``exp_results`` 汇总成 ``<project>/results/<run_id>/{metrics.json,table.md,curves.json}``，
  其中 ``curves.json`` 直接供 T3 画图使用。

远端交互一律通过 :mod:`sciforge.tools.remote` 的 ``SSHExecutor`` / ``make_executor``，
测试 monkeypatch 执行器即可，不会真的连服务器。
"""

from __future__ import annotations

import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------

RUNS_DIRNAME = "runs"
RESULTS_DIRNAME = "results"
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "experiments"

RUN_STATUSES = ("pending", "running", "done", "failed", "stopped")
TERMINAL_STATUSES = frozenset({"done", "failed", "stopped"})
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "failed", "stopped"}),
    "running": frozenset({"done", "failed", "stopped"}),
    "done": frozenset(),
    "failed": frozenset(),
    "stopped": frozenset(),
}

TZ_UTC8 = ZoneInfo("Asia/Shanghai")

_TEMPLATE_META = "template.yaml"


def now_utc8() -> str:
    """UTC+8 的 ISO 时间戳（秒级）。"""
    return datetime.now(TZ_UTC8).isoformat(timespec="seconds")


def _stamp_utc8() -> str:
    return datetime.now(TZ_UTC8).strftime("%Y%m%d-%H%M%S")


def slugify(text: str, *, default: str = "exp") -> str:
    cleaned = "".join(
        char if (char.isalnum() or char in "-_") else "-" for char in str(text or "").strip()
    )
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:60].strip("-_") or default


def new_run_id(prefix: str = "exp") -> str:
    """生成唯一 run_id：``<时间戳>-<slug>``。"""
    return f"{_stamp_utc8()}-{slugify(prefix, default='exp')}"


def runs_root(project_dir: str | Path) -> Path:
    return Path(project_dir).expanduser().resolve() / RUNS_DIRNAME


def results_root(project_dir: str | Path) -> Path:
    return Path(project_dir).expanduser().resolve() / RESULTS_DIRNAME


def run_dir_for(project_dir: str | Path, run_id: str) -> Path:
    return runs_root(project_dir) / str(run_id)


def load_yaml_file(path: str | Path) -> dict[str, Any]:
    """读取 YAML；不存在返回空 dict；格式错误抛 ``ValueError``。"""
    file = Path(path).expanduser()
    if not file.exists():
        return {}
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败：{file}\n{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"YAML 顶层应为 mapping：{file}")
    return data


def write_yaml_file(path: str | Path, data: dict[str, Any]) -> Path:
    file = Path(path).expanduser()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return file


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并 dict（override 优先；返回全新对象，不共享嵌套引用）。"""
    import copy

    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def set_nested(data: dict[str, Any], dotted_key: str, value: Any) -> dict[str, Any]:
    """``set_nested(d, "train.lr", 1e-3)`` → ``{"train": {"lr": 1e-3}}``。"""
    keys = [part for part in str(dotted_key).split(".") if part]
    if not keys:
        raise ValueError("参数名不能为空")
    current = data
    for key in keys[:-1]:
        node = current.get(key)
        if not isinstance(node, dict):
            node = {}
            current[key] = node
        current = node
    current[keys[-1]] = value
    return data


# ---------------------------------------------------------------------------
# git 信息
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> str:
    if shutil.which("git") is None:
        return ""
    try:
        proc = subprocess.run(  # noqa: S603 - 固定 git 参数
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def git_info(project_dir: str | Path) -> dict[str, Any]:
    """读取项目 git 状态；非 git 仓库/无提交时返回空 commit，不抛异常。"""
    cwd = Path(project_dir).expanduser()
    if not cwd.exists():
        return {"commit": "", "branch": "", "dirty": False}
    commit = _git(["rev-parse", "--short", "HEAD"], cwd)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    dirty = bool(_git(["status", "--porcelain"], cwd))
    return {"commit": commit, "branch": branch, "dirty": dirty}


# ---------------------------------------------------------------------------
# run.json 记录
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """一次实验运行；序列化到 ``runs/<run_id>/run.json``。"""

    run_id: str
    server: str = "local"
    template: str = "custom"
    command: str = ""
    status: str = "pending"
    log_path: str = ""
    started_at: str = ""
    git_commit: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    # ---- 扩展字段 ----
    project: str = ""
    run_dir: str = ""
    remote_dir: str = ""
    mode: str = ""
    job_id: str = ""
    error: str = ""
    finished_at: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sweep_id: str = ""
    exit_code: int | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = now_utc8()
        if not self.updated_at:
            self.updated_at = self.started_at
        if self.status not in RUN_STATUSES:
            raise ValueError(f"未知状态：{self.status}（支持 {', '.join(RUN_STATUSES)}）")

    # -- 状态机 -----------------------------------------------------------
    def transition(self, status: str, *, error: str | None = None) -> RunRecord:
        if status not in RUN_STATUSES:
            raise ValueError(f"未知状态：{status}")
        if status != self.status and status not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"非法状态迁移：{self.status} → {status}")
        self.status = status
        self.updated_at = now_utc8()
        if status in TERMINAL_STATUSES:
            self.finished_at = self.updated_at
        if error:
            self.error = str(error)
        return self

    def add_metric(self, key: str, value: Any) -> None:
        self.metrics[str(key)] = value
        self.updated_at = now_utc8()

    def touch(self) -> None:
        self.updated_at = now_utc8()

    # -- 序列化 -----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "server": self.server,
            "template": self.template,
            "command": self.command,
            "status": self.status,
            "log_path": self.log_path,
            "started_at": self.started_at,
            "git_commit": self.git_commit,
            "metrics": self.metrics,
            "updated_at": self.updated_at,
            "project": self.project,
            "run_dir": self.run_dir,
            "remote_dir": self.remote_dir,
            "mode": self.mode,
            "job_id": self.job_id,
            "error": self.error,
            "finished_at": self.finished_at,
            "params": self.params,
            "sweep_id": self.sweep_id,
            "exit_code": self.exit_code,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        known = {field_name for field_name in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {key: value for key, value in (data or {}).items() if key in known}
        return cls(**payload)

    def save(self, project_dir: str | Path | None = None) -> Path:
        if project_dir is not None:
            directory = run_dir_for(project_dir, self.run_id)
        elif self.run_dir:
            directory = Path(self.run_dir)
        else:
            directory = Path(RUNS_DIRNAME) / self.run_id
        directory.mkdir(parents=True, exist_ok=True)
        self.run_dir = str(directory)
        self.updated_at = now_utc8()
        path = directory / "run.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def save_run(record: RunRecord, project_dir: str | Path) -> Path:
    return record.save(project_dir)


def load_run(project_dir: str | Path, run_id: str | Path) -> RunRecord | None:
    """按 run_id / 目录 / run.json 路径加载记录。"""
    raw = str(run_id)
    candidates: list[Path] = []
    path = Path(raw).expanduser()
    if path.exists():
        candidates.append(path / "run.json" if path.is_dir() else path)
    else:
        candidates.append(runs_root(project_dir) / raw / "run.json")
        candidates.append(run_dir_for(project_dir, raw) / "run.json")
    for candidate in candidates:
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return RunRecord.from_dict(data)
    # 前缀匹配（run_id 只给了后半段时）
    matches = sorted(runs_root(project_dir).glob(f"*{raw}*/run.json"))
    if len(matches) == 1:
        data = json.loads(matches[0].read_text(encoding="utf-8"))
        return RunRecord.from_dict(data)
    return None


def list_runs(project_dir: str | Path, *, limit: int = 50, status: str | None = None) -> list[RunRecord]:
    """列出 run 记录（按 started_at 倒序）。"""
    root = runs_root(project_dir)
    records: list[RunRecord] = []
    if not root.exists():
        return records
    for run_json in sorted(root.glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            record = RunRecord.from_dict(json.loads(run_json.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if status and record.status != status:
            continue
        records.append(record)
    records.sort(key=lambda record: record.started_at, reverse=True)
    return records[: max(1, int(limit))]


def latest_run(project_dir: str | Path, *, status: str | None = None) -> RunRecord | None:
    records = list_runs(project_dir, limit=1, status=status)
    return records[0] if records else None


# ---------------------------------------------------------------------------
# 模板与脚手架
# ---------------------------------------------------------------------------


def list_templates() -> list[dict[str, Any]]:
    """列出内置实验模板（读 ``sciforge/templates/experiments/*/template.yaml``）。"""
    templates: list[dict[str, Any]] = []
    if not TEMPLATES_DIR.is_dir():
        return templates
    for directory in sorted(TEMPLATES_DIR.iterdir()):
        if not directory.is_dir() or directory.name.startswith((".", "_")):
            continue
        meta_path = directory / _TEMPLATE_META
        meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("name", directory.name)
        meta.setdefault("description", "")
        meta["path"] = str(directory)
        templates.append(meta)
    return templates


def get_template_meta(template: str) -> dict[str, Any]:
    for meta in list_templates():
        if meta.get("name") == template:
            return meta
    available = ", ".join(str(meta.get("name")) for meta in list_templates()) or "无"
    raise ValueError(f"未知实验模板：{template}（可用：{available}）")


def _render_template(text: str, context: dict[str, Any]) -> str:
    from jinja2 import StrictUndefined, Template

    return Template(text, undefined=StrictUndefined, keep_trailing_newline=True).render(**context)


def scaffold_experiment(
    dest: str | Path,
    *,
    template: str = "generic",
    name: str | None = None,
    dataset: str | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """按模板生成可训练项目骨架，返回生成信息（纯函数式，便于测试）。"""
    meta = get_template_meta(template)
    template_dir = Path(meta["path"])
    destination = Path(dest).expanduser().resolve()
    project_name = slugify(name or destination.name or template, default=template)
    chosen_dataset = str(dataset or meta.get("default_dataset") or "synthetic")
    chosen_model = str(model or meta.get("default_model") or "baseline")

    if destination.exists() and any(destination.iterdir()):
        if not force:
            raise FileExistsError(
                f"目标目录非空：{destination}。换一个 --name，或加 --force 覆盖（会保留其他文件）。"
            )
    destination.mkdir(parents=True, exist_ok=True)

    context = {
        "name": project_name,
        "project": project_name,
        "template": template,
        "dataset": chosen_dataset,
        "model": chosen_model,
        "created_at": now_utc8(),
    }
    files: list[str] = []
    for source in sorted(template_dir.rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(template_dir)
        if relative.name == _TEMPLATE_META or "__pycache__" in relative.parts:
            continue
        target_relative = relative.with_suffix("") if relative.suffix == ".j2" else relative
        target = destination / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".j2":
            target.write_text(
                _render_template(source.read_text(encoding="utf-8"), context), encoding="utf-8"
            )
        else:
            shutil.copy2(source, target)
        mode = os.access(source, os.X_OK)
        if mode or relative.suffix == ".sh":
            target.chmod(0o755)
        files.append(str(target_relative))

    experiment_meta = {
        "name": project_name,
        "template": template,
        "dataset": chosen_dataset,
        "model": chosen_model,
        "created_at": context["created_at"],
    }
    (destination / "experiment.json").write_text(
        json.dumps(experiment_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files.insert(0, "experiment.json")
    return {
        "project_dir": str(destination),
        "template": template,
        "name": project_name,
        "dataset": chosen_dataset,
        "model": chosen_model,
        "files": files,
        "smoke_command": "bash scripts/smoke.sh",
        "train_command": "bash scripts/run.sh",
    }


def describe_scaffold(info: dict[str, Any]) -> str:
    files = info.get("files", [])
    preview = "\n".join(f"  - {name}" for name in files[:20])
    if len(files) > 20:
        preview += f"\n  …（共 {len(files)} 个文件）"
    return (
        f"已生成实验骨架：{info['project_dir']}\n"
        f"模板: {info['template']} | 数据: {info['dataset']} | 模型: {info['model']}\n"
        f"{preview}\n\n"
        f"下一步：\n"
        f"  1. 离线自检：cd {info['project_dir']} && {info['smoke_command']}\n"
        f"  2. 本地训练：cd {info['project_dir']} && {info['train_command']}\n"
        f"  3. 远端训练：sciforge exp launch --server gpu1 --script scripts/run.sh"
    )


# ---------------------------------------------------------------------------
# 执行器解析与启动
# ---------------------------------------------------------------------------


def _resolve_executor(
    ctx: ToolContext,
    server_name: str | None,
    *,
    local: bool = False,
    workdir: str | Path | None = None,
):
    """返回 (executor, server 或 None)；本地模式 server=None。"""
    from sciforge.tools.remote import LocalExecutor, make_executor

    if local:
        return LocalExecutor(workdir=workdir or ctx.project_dir), None
    server = ctx.get_server(server_name)
    if server is None:
        raise ValueError(
            "未配置 GPU 服务器。请先 `sciforge ssh add gpu1 --host <host> --user <user> --key <key>`，"
            "或加 --local 在本地启动。"
        )
    return make_executor(server), server


def _executor_for_record(ctx: ToolContext, record: RunRecord):
    """按 run.json 的 server 字段重建执行器（不抛异常）。"""
    from sciforge.tools.remote import LocalExecutor, SSHExecutor

    if record.server in ("", "local", None):
        return LocalExecutor(workdir=ctx.project_dir), None
    server = ctx.get_server(record.server)
    if server is None:
        return None, None
    return SSHExecutor(server), server


def _script_launch_command(script: str) -> str:
    """按脚本后缀选择解释器：.py → python，其余 → bash。"""
    suffix = Path(script).suffix.lower()
    interpreter = "python" if suffix == ".py" else "bash"
    return f"{interpreter} {script}"


def _validate_config(config_path: Path) -> dict[str, Any] | None:
    """校验实验 YAML 配置；非法时抛 ValueError。"""
    if not config_path.exists():
        return None
    return load_yaml_file(config_path)


def _launch_prepared(
    ctx: ToolContext,
    record: RunRecord,
    *,
    executor: Any,
    server: Any,
    command: str,
    exp_dir: Path,
    sync: bool,
    sync_excludes: list[str] | None = None,
    smoke: bool = False,
) -> RunRecord:
    """已确定 executor/command 的启动逻辑：可选同步 → 后台启动 → 写 run.json。"""
    is_remote = server is not None
    project_dir = Path(ctx.project_dir).resolve()
    run_id = record.run_id
    local_run_dir = run_dir_for(project_dir, run_id)
    local_run_dir.mkdir(parents=True, exist_ok=True)
    record.run_dir = str(local_run_dir)
    record.project = str(project_dir)
    record.template = record.template or "custom"
    record.git_commit = git_info(project_dir)["commit"]
    record.command = command
    record.server = server.name if server is not None else "local"

    if is_remote:
        remote_workdir = str(executor.workdir)
        remote_run_dir = posixpath.join(remote_workdir, RUNS_DIRNAME, run_id)
        log_path = posixpath.join(remote_run_dir, "train.log")
        run_dir_token = posixpath.join(RUNS_DIRNAME, run_id)
        record.remote_dir = remote_workdir
        record.log_path = log_path
        record.mode = str(getattr(server, "scheduler", "none") or "none")
    else:
        remote_run_dir = str(local_run_dir)
        log_path = str(local_run_dir / "train.log")
        run_dir_token = str(local_run_dir)
        record.remote_dir = str(project_dir)
        record.log_path = log_path
        record.mode = "local-nohup"

    try:
        if is_remote:
            ensure = getattr(executor, "ensure_workdir", None)
            if callable(ensure):
                ensure()
            if sync:
                excludes = list(getattr(server, "sync_excludes", []) or []) + list(
                    sync_excludes or []
                )
                upload = executor.upload_paths([str(exp_dir) + "/"], remote_workdir, excludes=excludes)
                if not getattr(upload, "ok", False):
                    raise RuntimeError(
                        f"同步项目到 {server.name} 失败：{(upload.stderr or upload.stdout).strip()[:500]}"
                    )
                # sweep 的每 run 配置不在项目同步范围内，单独上传到远端 run 目录
                local_config = local_run_dir / "config.yaml"
                if local_config.exists():
                    executor.upload_paths(
                        [str(local_config)],
                        posixpath.join(remote_workdir, RUNS_DIRNAME, run_id),
                        excludes=[],
                    )
        record.transition("running")
        scheduler = str(getattr(server, "scheduler", "none") or "none") if server is not None else "none"
        if is_remote and scheduler == "slurm":
            from sciforge.tools.remote import render_sbatch_script

            script = render_sbatch_script(
                job_name=run_id,
                command=command,
                workdir=str(executor.workdir),
                conda_env=getattr(server, "conda_env", None),
                partition=getattr(server, "partition", None),
                gpu_type=getattr(server, "gpu_type", None),
                log_path=log_path,
                env=getattr(server, "env", None),
            )
            job = executor.submit_slurm(script, run_id=run_id, log_path=log_path)
            job.setdefault("mode", "slurm")
        else:
            extra_env = {"SCIFORGE_RUN_DIR": run_dir_token, "SCIFORGE_RUN_ID": run_id}
            if not is_remote:
                # 本地启动用当前解释器，避免 PATH 里没有 python 的机器跑不起来
                extra_env["SCIFORGE_PYTHON"] = sys.executable
            job = executor.run_detached(
                run_id,
                command,
                log_path,
                extra_env=extra_env,
            )
        record.job_id = str(job.get("job_id") or "")
        record.mode = str(job.get("mode") or record.mode)
        record.notes = str(job.get("warning") or "")
        if smoke:
            record.notes = ("smoke 模式（CPU 无数据自检）\n" + record.notes).strip()
        if job.get("tmux_error"):
            record.notes = (record.notes + f"\ntmux 不可用，已退回 nohup：{job['tmux_error']}").strip()
        save_run(record, project_dir)
        return record
    except Exception as exc:  # noqa: BLE001 - 启动失败也要落盘 run.json
        record.error = f"{type(exc).__name__}: {exc}"
        try:
            record.transition("failed", error=record.error)
        except ValueError:
            record.status = "failed"
            record.error = str(exc)
            record.updated_at = now_utc8()
            record.finished_at = record.updated_at
        save_run(record, project_dir)
        return record


def launch_experiment(
    ctx: ToolContext,
    *,
    server: str | None = None,
    name: str | None = None,
    script: str | None = None,
    command: str | None = None,
    template: str | None = None,
    exp_dir: str | Path | None = None,
    sync: bool = True,
    local: bool = False,
    smoke: bool = False,
) -> RunRecord:
    """启动一次实验：校验配置 → 同步 → 后台运行 → 写 run.json。

    ``smoke=True`` 时优先执行模板的 ``scripts/smoke.sh``（CPU 无数据，1 分钟内完成），
    用于在没有 GPU/torch 的机器上验证 launch→status→results 全链路。
    """
    project_dir = Path(ctx.project_dir).resolve()
    directory = Path(exp_dir).expanduser() if exp_dir else project_dir
    if not directory.is_absolute():
        directory = project_dir / directory
    directory = directory.resolve()
    if not directory.exists():
        raise FileNotFoundError(f"实验目录不存在：{directory}")

    # 1) 命令解析
    if command:
        launch_command = str(command)
    elif script:
        script_path = Path(script)
        if not script_path.is_absolute():
            script_path = directory / script_path
        if not script_path.exists():
            raise FileNotFoundError(f"启动脚本不存在：{script_path}")
        launch_command = _script_launch_command(Path(script).as_posix())
    elif smoke and (directory / "scripts" / "smoke.sh").exists():
        launch_command = "bash scripts/smoke.sh"
    elif (directory / "scripts" / "run.sh").exists():
        launch_command = "bash scripts/run.sh"
    else:
        raise FileNotFoundError(
            f"{directory} 下没有 scripts/run.sh；请用 --script 或 --command 指定启动方式。"
        )

    # 2) 配置校验（存在就解析，格式错误直接失败）
    detected_template = template
    for candidate in ("configs/default.yaml", "configs/config.yaml", "config.yaml"):
        config_path = directory / candidate
        if config_path.exists():
            loaded = _validate_config(config_path)
            if not detected_template:
                detected_template = str((loaded or {}).get("template") or "")
            break
    experiment_meta = directory / "experiment.json"
    if not detected_template and experiment_meta.exists():
        try:
            detected_template = str(
                (json.loads(experiment_meta.read_text(encoding="utf-8")) or {}).get("template") or ""
            )
        except (OSError, json.JSONDecodeError):
            pass
    detected_template = detected_template or (template or "custom")

    # 3) 执行器（本地实验以实验目录为工作目录）
    executor, server_obj = _resolve_executor(ctx, server, local=local, workdir=directory)

    run_id = new_run_id(name or Path(directory).name or detected_template)
    record = RunRecord(
        run_id=run_id,
        server=server_obj.name if server_obj is not None else "local",
        template=str(detected_template),
        command=launch_command,
        log_path="",
        started_at=now_utc8(),
    )
    excludes = [
        f"{RUNS_DIRNAME}/",
        f"{RESULTS_DIRNAME}/",
        ".git/",
        f"{RUNS_DIRNAME}",
        f"{RESULTS_DIRNAME}",
    ]
    return _launch_prepared(
        ctx,
        record,
        executor=executor,
        server=server_obj,
        command=launch_command,
        exp_dir=directory,
        sync=sync,
        sync_excludes=excludes,
        smoke=smoke,
    )


def describe_launch(record: RunRecord) -> str:
    lines = [
        f"实验已启动：{record.run_id}",
        f"server: {record.server} | template: {record.template} | mode: {record.mode}",
        f"command: {record.command}",
        f"log: {record.log_path}",
        f"job_id: {record.job_id or '-'}",
        f"started_at: {record.started_at}",
    ]
    if record.notes:
        lines.append(f"note: {record.notes}")
    lines += [
        "",
        "下一步：",
        f"  sciforge exp status {record.run_id}",
        f"  sciforge exp logs {record.run_id}",
        f"  sciforge exp results {record.run_id}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 状态推断与刷新
# ---------------------------------------------------------------------------

_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)|^Error:|RuntimeError:|CUDA out of memory", re.M)
_DONE_PATTERNS = (
    "SMOKE OK",
    "training finished",
    "finished:",
    "Training complete",
    "done.",
)


def infer_status_from_log(text: str) -> str | None:
    """从日志文本粗判运行状态；无法判断返回 None。"""
    body = text or ""
    if _TRACEBACK_RE.search(body):
        return "failed"
    if any(pattern in body for pattern in _DONE_PATTERNS):
        return "done"
    return None


def refresh_record(ctx: ToolContext, record: RunRecord, *, tail_lines: int = 40) -> RunRecord:
    """尝试刷新运行状态（进程存活 / 日志推断 / metrics 存在）。失败只记录 unknown。"""
    if record.status in TERMINAL_STATUSES:
        return record
    project_dir = Path(ctx.project_dir).resolve()
    executor, _server = _executor_for_record(ctx, record)
    alive: bool | None = None
    log_text = ""
    if executor is not None:
        try:
            if record.job_id:
                state = executor.job_state(record.job_id)
                alive = state.get("alive")
            tail = executor.tail_logs(record.log_path, lines=tail_lines)
            log_text = tail.stdout or ""
        except Exception:  # noqa: BLE001 - 状态刷新失败不应崩
            alive = None
    metrics_exists = (run_dir_for(project_dir, record.run_id) / "metrics.json").exists()
    inferred = infer_status_from_log(log_text)
    new_status: str | None = None
    if alive is False:
        if inferred == "done" or metrics_exists:
            new_status = "done"
        elif inferred == "failed":
            new_status = "failed"
        else:
            new_status = "failed"
    if new_status and new_status != record.status:
        try:
            record.transition(new_status)
        except ValueError:
            record.status = new_status
            record.updated_at = now_utc8()
        if new_status == "done" and metrics_exists:
            try:
                raw_metrics = json.loads(
                    (run_dir_for(project_dir, record.run_id) / "metrics.json").read_text(encoding="utf-8")
                )
                canonical = normalize_metrics(
                    raw_metrics,
                    run_id=record.run_id,
                    source=str(run_dir_for(project_dir, record.run_id) / "metrics.json"),
                )
                record.metrics = {
                    **{key: value for key, value in canonical["summary"].items() if _is_number(value)},
                    "num_epochs": canonical["num_epochs"],
                }
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        if log_text:
            record.notes = (record.notes + "\n" if record.notes else "") + log_text.strip().splitlines()[-1][:300]
        save_run(record, project_dir)
    return record


# ---------------------------------------------------------------------------
# 结果汇总
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_history(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """从多种 metrics.json 结构中抽取 per-epoch 历史。"""
    for key in ("epochs", "history", "metrics"):
        value = raw.get(key)
        if isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
            return [dict(row) for row in value]
    # {"train_loss": [...], "val_acc": [...]} 形式
    list_keys = [
        key for key, value in raw.items() if isinstance(value, list) and value and all(_is_number(v) for v in value)
    ]
    if list_keys:
        length = min(len(raw[key]) for key in list_keys)
        rows: list[dict[str, Any]] = []
        for index in range(length):
            row = {key: raw[key][index] for key in list_keys}
            row.setdefault("epoch", index + 1)
            rows.append(row)
        return rows
    return []


def extract_summary(raw: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    """提取 best/final 等标量摘要。"""
    summary: dict[str, Any] = {}
    for key in ("best", "final", "summary", "result", "results"):
        value = raw.get(key)
        if isinstance(value, dict):
            summary.update(value)
    if not summary and history:
        last = history[-1]
        summary = {key: value for key, value in last.items() if _is_number(value)}
        numeric_keys = [
            key
            for key in last
            if _is_number(last.get(key)) and key not in ("epoch",)
        ]
        if "val_acc" in numeric_keys:
            best = max(history, key=lambda row: row.get("val_acc", float("-inf")))
            summary["best_epoch"] = best.get("epoch")
            summary["best_val_acc"] = best.get("val_acc")
        elif "val_loss" in numeric_keys:
            best = min(history, key=lambda row: row.get("val_loss", float("inf")))
            summary["best_epoch"] = best.get("epoch")
            summary["best_val_loss"] = best.get("val_loss")
    for key, value in raw.items():
        if _is_number(value) and key not in summary:
            summary[key] = value
    return summary


def normalize_metrics(raw: dict[str, Any], *, run_id: str, source: str) -> dict[str, Any]:
    """把任意 metrics.json 规范化成 T3 可消费的结构。"""
    if not isinstance(raw, dict):
        raise ValueError("metrics.json 顶层必须是对象（mapping）")
    history = extract_history(raw)
    summary = extract_summary(raw, history)
    metric_names = sorted(
        {key for row in history for key, value in row.items() if _is_number(value) and key != "epoch"}
    )
    return {
        "run_id": run_id,
        "source": source,
        "history": history,
        "summary": summary,
        "metric_names": metric_names,
        "num_epochs": len(history),
        "raw": raw,
    }


def render_markdown_table(
    history: list[dict[str, Any]], summary: dict[str, Any], *, title: str | None = None
) -> str:
    """生成 Markdown 三线表（顶部/底部用空行强调，表内无多余竖线）。"""
    lines: list[str] = []
    if title:
        lines += [f"### {title}", ""]
    if history:
        columns = sorted(
            {key for row in history for key, value in row.items() if _is_number(value) and key != "epoch"}
        )
        header = ["Epoch", *columns]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---:"] * len(header)) + " |")
        for row in history:
            cells = [str(row.get("epoch", ""))]
            for column in columns:
                value = row.get(column)
                cells.append("" if value is None else _format_number(value))
            lines.append("| " + " | ".join(cells) + " |")
        if summary:
            lines += ["", "**摘要**", "", "| 指标 | 值 |", "| --- | ---: |"]
            for key, value in summary.items():
                if _is_number(value):
                    lines.append(f"| {key} | {_format_number(value)} |")
    elif summary:
        lines.append("| 指标 | 值 |")
        lines.append("| --- | ---: |")
        for key, value in summary.items():
            cells = _format_number(value) if _is_number(value) else str(value)
            lines.append(f"| {key} | {cells} |")
    else:
        lines.append("（metrics.json 中没有可解析的指标）")
    return "\n".join(lines)


def _format_number(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return "n/a"
    if abs(number) >= 1000:
        return f"{number:.2f}"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def build_curve_data(
    run_id: str, history: list[dict[str, Any]], *, title: str | None = None
) -> dict[str, Any]:
    """生成曲线数据 JSON（直接喂给 T3 的 figure_plot）。"""
    x_values = [row.get("epoch", index + 1) for index, row in enumerate(history)]
    series: list[dict[str, Any]] = []
    metric_names = sorted(
        {key for row in history for key, value in row.items() if _is_number(value) and key != "epoch"}
    )
    for name in metric_names:
        y_values = [row.get(name) for row in history]
        if any(value is None for value in y_values):
            continue
        series.append({"name": name, "x": x_values, "y": y_values, "kind": "line"})
    # 同时兼容 T3 ``render_plot`` 的 spec 键（kind/xlabel/ylabel）与自描述键
    return {
        "run_id": run_id,
        "kind": "line",
        "title": title or f"{run_id} metrics",
        "xlabel": "epoch",
        "ylabel": "value",
        "x_label": "epoch",
        "y_label": "value",
        "x": x_values,
        "series": series,
        "style": "neurips",
        "legend": True,
    }


def _find_metrics_candidates(run_dir: Path) -> list[Path]:
    candidates = [run_dir / "metrics.json", run_dir / "results" / "metrics.json"]
    candidates += sorted(run_dir.glob("*/metrics.json"))
    candidates += sorted(run_dir.rglob("metrics.json"))
    seen: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and candidate not in seen:
            seen.append(candidate)
    return seen


def collect_results(
    ctx: ToolContext,
    *,
    run_id: str | None = None,
    metrics_path: str | Path | None = None,
    download: bool = True,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """收集 metrics → ``results/<run_id>/`` 写 metrics.json/table.md/curves.json。

    ``out_dir`` 可覆盖结果根目录（默认项目 ``results/``）。
    """
    project_dir = Path(ctx.project_dir).resolve()
    record: RunRecord | None = None
    if run_id:
        record = load_run(project_dir, run_id)
    if record is None and metrics_path is None:
        record = latest_run(project_dir, status="done") or latest_run(project_dir)
    resolved_run_id = str(run_id or (record.run_id if record else "results"))

    source_path: Path | None = None
    if metrics_path:
        candidate = Path(metrics_path).expanduser()
        if not candidate.is_absolute():
            candidate = project_dir / candidate
        if candidate.is_file():
            source_path = candidate
    run_dir = run_dir_for(project_dir, resolved_run_id)
    if source_path is None:
        candidates = _find_metrics_candidates(run_dir if run_dir.exists() else project_dir)
        if candidates:
            source_path = candidates[0]
    remote_error = ""
    if source_path is None and record is not None and download:
        executor, _server = _executor_for_record(ctx, record)
        if executor is not None and record.server not in ("", "local", None):
            remote_candidates = [
                posixpath.join(record.remote_dir or "", RUNS_DIRNAME, record.run_id, "metrics.json"),
                posixpath.join(record.remote_dir or "", record.run_id, "metrics.json"),
            ]
            local_dir = run_dir
            local_dir.mkdir(parents=True, exist_ok=True)
            for remote_path in remote_candidates:
                if not remote_path:
                    continue
                try:
                    downloaded = executor.download_paths([remote_path], local_dir, excludes=[])
                except Exception as exc:  # noqa: BLE001
                    remote_error = f"{type(exc).__name__}: {exc}"
                    continue
                if getattr(downloaded, "ok", False) and (local_dir / "metrics.json").exists():
                    source_path = local_dir / "metrics.json"
                    break
    if source_path is None:
        hint = f"远端下载失败：{remote_error}" if remote_error else ""
        raise FileNotFoundError(
            f"找不到 {resolved_run_id} 的 metrics.json。请确认训练已把 metrics.json 写到 "
            f"{run_dir}/metrics.json（模板 scripts/run.sh 通过 SCIFORGE_RUN_DIR 保证这一点）。{hint}"
        )

    raw = json.loads(source_path.read_text(encoding="utf-8"))
    canonical = normalize_metrics(raw, run_id=resolved_run_id, source=str(source_path))

    if out_dir:
        results_base = Path(out_dir).expanduser()
        if not results_base.is_absolute():
            results_base = project_dir / results_base
        results_base = results_base.resolve()
        if not _is_within(results_base, project_dir) and not _is_within(results_base, Path(ctx.data_dir).resolve()):
            raise PermissionError(f"拒绝写入项目/数据目录之外的结果目录：{results_base}")
    else:
        results_base = results_root(project_dir)
    result_dir = results_base / resolved_run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    metrics_out = result_dir / "metrics.json"
    metrics_out.write_text(
        json.dumps(
            {
                "run_id": canonical["run_id"],
                "source": canonical["source"],
                "num_epochs": canonical["num_epochs"],
                "metric_names": canonical["metric_names"],
                "history": canonical["history"],
                "summary": canonical["summary"],
                "raw": canonical["raw"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    table_text = render_markdown_table(
        canonical["history"], canonical["summary"], title=f"{resolved_run_id} 实验结果"
    )
    table_out = result_dir / "table.md"
    table_out.write_text(table_text + "\n", encoding="utf-8")
    curves = build_curve_data(resolved_run_id, canonical["history"])
    curves_out = result_dir / "curves.json"
    curves_out.write_text(json.dumps(curves, ensure_ascii=False, indent=2), encoding="utf-8")

    if record is not None:
        record.metrics = {
            **{key: value for key, value in canonical["summary"].items() if _is_number(value)},
            "num_epochs": canonical["num_epochs"],
        }
        record.metrics["results_dir"] = str(result_dir)
        if record.status not in TERMINAL_STATUSES:
            try:
                record.transition("done")
            except ValueError:
                record.status = "done"
                record.updated_at = now_utc8()
        record.touch()
        save_run(record, project_dir)

    return {
        "run_id": resolved_run_id,
        "source": str(source_path),
        "result_dir": str(result_dir),
        "metrics_path": str(metrics_out),
        "table_path": str(table_out),
        "curves_path": str(curves_out),
        "table": table_text,
        "curves": curves,
        "summary": canonical["summary"],
        "num_epochs": canonical["num_epochs"],
    }


# ---------------------------------------------------------------------------
# 参数 sweep
# ---------------------------------------------------------------------------


def expand_grid(base: dict[str, Any], grid: dict[str, Any]) -> list[dict[str, Any]]:
    """把 ``{"train.lr": [...], "train.batch_size": [...]}`` 展开成配置列表。"""
    if not isinstance(grid, dict) or not grid:
        return [dict(base)]
    keys = list(grid.keys())
    value_lists: list[list[Any]] = []
    for key in keys:
        values = grid[key]
        if not isinstance(values, (list, tuple)):
            values = [values]
        value_lists.append(list(values))
    configs: list[dict[str, Any]] = []
    for combination in product(*value_lists):
        config = deep_merge({}, base)
        for key, value in zip(keys, combination, strict=False):
            set_nested(config, key, value)
        configs.append(config)
    return configs


def _param_token(params: dict[str, Any]) -> str:
    parts: list[str] = []

    def walk(node: dict[str, Any], prefix: str = "") -> None:
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                walk(value, path)
            else:
                short = path.split(".")[-1]
                parts.append(f"{short}={value}")

    walk(params)
    return slugify("-".join(parts), default="params")[:60]


def sweep_experiments(
    ctx: ToolContext,
    *,
    config_path: str | Path,
    server: str | None = None,
    launch: bool = False,
    max_parallel: int | None = None,
    sync: bool = True,
    local: bool = False,
) -> dict[str, Any]:
    """读取 sweep YAML，为每个参数组合生成 run 记录；(可选) 按并发上限启动。"""
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = Path(ctx.project_dir) / path
    sweep = load_yaml_file(path)
    if not sweep:
        raise ValueError(f"sweep 配置为空或不存在：{path}")
    grid = sweep.get("grid") or sweep.get("parameters") or {}
    if not isinstance(grid, dict) or not grid:
        raise ValueError("sweep 配置缺少 grid（参数网格）")
    base = sweep.get("base") or {}
    if not isinstance(base, dict):
        raise ValueError("sweep 配置的 base 必须是 mapping")

    project_dir = Path(ctx.project_dir).resolve()
    # 用项目默认配置作为底，保证 sweep 生成的 config 是完整可训练的
    template_config: dict[str, Any] = {}
    for candidate in ("configs/default.yaml", "configs/config.yaml"):
        config_file = project_dir / candidate
        if config_file.exists():
            template_config = load_yaml_file(config_file)
            break
    base = deep_merge(template_config, base)
    sweep_name = slugify(str(sweep.get("name") or path.stem or "sweep"), default="sweep")
    template = str(sweep.get("template") or "custom")
    command = str(sweep.get("command") or "bash scripts/run.sh")
    combos = expand_grid(base, grid)
    stamp = _stamp_utc8()

    executor = None
    server_obj = None
    if launch:
        executor, server_obj = _resolve_executor(
            ctx, server or sweep.get("server"), local=local, workdir=project_dir
        )
    target_server = (
        server_obj.name if server_obj is not None else ("local" if local or not launch else "unlaunched")
    )

    plan: list[RunRecord] = []
    for index, combo in enumerate(combos, start=1):
        run_id = f"{sweep_name}-{stamp}-{index:02d}-{_param_token(combo)}"
        record = RunRecord(
            run_id=run_id,
            server=str(sweep.get("server") or target_server) if not local else "local",
            template=template,
            command=command,
            status="pending",
            started_at=now_utc8(),
            params=combo,
            sweep_id=sweep_name,
            metrics={},
        )
        local_run_dir = run_dir_for(project_dir, run_id)
        local_run_dir.mkdir(parents=True, exist_ok=True)
        record.run_dir = str(local_run_dir)
        record.project = str(project_dir)
        write_yaml_file(local_run_dir / "config.yaml", combo)
        if "configs/default.yaml" not in command and "--config" not in command:
            record.command = f"{command} --config {RUNS_DIRNAME}/{run_id}/config.yaml"
        record.git_commit = git_info(project_dir)["commit"]
        record.log_path = str(local_run_dir / "train.log")
        save_run(record, project_dir)
        plan.append(record)

    launched: list[str] = []
    if launch and executor is not None:
        limit = int(max_parallel or getattr(server_obj, "max_parallel_jobs", None) or 1)
        limit = max(1, limit)
        for record in plan[:limit]:
            _launch_prepared(
                ctx,
                record,
                executor=executor,
                server=server_obj,
                command=record.command,
                exp_dir=Path(ctx.project_dir).resolve(),
                sync=sync,
                sync_excludes=[f"{RUNS_DIRNAME}/", f"{RESULTS_DIRNAME}/"],
            )
            launched.append(record.run_id)

    rows = []
    for record in plan:
        params = json.dumps(record.params, ensure_ascii=False)
        rows.append(f"| {record.run_id} | {record.status} | {params} |")
    table = "\n".join(["| run_id | status | params |", "| --- | --- | --- |", *rows])
    return {
        "sweep": sweep_name,
        "path": str(path),
        "num_runs": len(plan),
        "launched": launched,
        "runs": [record.to_dict() for record in plan],
        "table": table,
        "max_parallel": int(max_parallel or getattr(server_obj, "max_parallel_jobs", 0) or 0) if launch else None,
    }


def describe_sweep(info: dict[str, Any]) -> str:
    launched = info.get("launched") or []
    lines = [
        f"参数扫描 {info['sweep']}：{info['num_runs']} 组配置",
        f"配置文件: {info['path']}",
    ]
    if launched:
        limit = info.get("max_parallel")
        lines.append(f"已启动 {len(launched)} 个（并发上限 {limit}）：")
        lines += [f"  - {run_id}" for run_id in launched]
        pending = info["num_runs"] - len(launched)
        if pending:
            lines.append(f"其余 {pending} 个状态为 pending，可再次 `sciforge exp sweep ... --launch` 或手动 launch。")
    else:
        lines.append("当前仅生成 run.json（pending），加 `--launch` 才会真正提交。")
    return "\n".join(lines) + "\n\n" + str(info.get("table") or "")


# ---------------------------------------------------------------------------
# 工具注册
# ---------------------------------------------------------------------------


def _project_path(ctx: ToolContext, raw: str | Path | None, *, default: Path | None = None) -> Path:
    """解析项目内路径（禁止写到项目/数据目录外）。"""
    if raw is None:
        if default is None:
            raise ValueError("缺少路径参数")
        candidate = default
    else:
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = Path(ctx.project_dir) / candidate
    candidate = candidate.resolve()
    allowed = [Path(ctx.project_dir).resolve(), Path(ctx.data_dir).resolve()]
    if not any(_is_within(candidate, base) for base in allowed):
        raise PermissionError(
            f"拒绝写入项目/数据目录之外的路径：{candidate}。如需特殊路径请先在项目内创建后移动。"
        )
    return candidate


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


class ExpScaffoldTool(BaseTool):
    name = "exp_scaffold"
    description = (
        "按模板生成可训练的实验项目骨架（cv-classification / nlp-finetune / llm-sft / generic），"
        "包含 configs/*.yaml、src/{data,model,train,evaluate}.py、scripts/run.sh、requirements.txt、README。"
        "生成后可用 `bash scripts/smoke.sh` 在 CPU 无数据条件下 1 分钟内自检。"
    )
    tags = ("experiment",)
    parameters = {
        "type": "object",
        "properties": {
            "template": {
                "type": "string",
                "enum": ["cv-classification", "nlp-finetune", "llm-sft", "generic"],
                "default": "generic",
            },
            "name": {"type": "string", "description": "实验/项目名（也用作目录名）"},
            "dest": {"type": "string", "description": "生成目录（相对项目目录，默认 experiments/<name>）"},
            "dataset": {"type": "string", "description": "数据集名，覆盖模板默认值"},
            "model": {"type": "string", "description": "模型名，覆盖模板默认值"},
            "force": {"type": "boolean", "default": False, "description": "目标目录非空时覆盖"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        template = str(kwargs.get("template") or "generic")
        name = str(kwargs.get("name") or "my-exp")
        default_dest = Path(ctx.project_dir) / "experiments" / slugify(name)
        try:
            dest = _project_path(ctx, kwargs.get("dest"), default=default_dest)
            info = scaffold_experiment(
                dest,
                template=template,
                name=name,
                dataset=kwargs.get("dataset"),
                model=kwargs.get("model"),
                force=bool(kwargs.get("force")),
            )
        except (ValueError, FileExistsError, PermissionError) as exc:
            return ToolResult.failure(str(exc))
        artifacts = [str(Path(info["project_dir"]) / relative) for relative in info["files"] if relative.endswith((".py", ".yaml", ".sh", ".md", "requirements.txt", "experiment.json"))]
        return ToolResult.success(describe_scaffold(info), data=info, artifacts=artifacts)


class ExpLaunchTool(BaseTool):
    name = "exp_launch"
    description = (
        "启动一次训练实验：校验配置 → 可选 rsync 同步到 GPU 服务器 → tmux/nohup 后台运行 → "
        "写 runs/<run_id>/run.json（server/command/status/log_path/git_commit/时间戳）。"
        "返回 run_id 与查看状态/日志/结果的命令；不会阻塞等待训练结束。"
    )
    tags = ("experiment", "remote")
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "GPU 服务器名（缺省用第一个；无配置可加 local=true）"},
            "name": {"type": "string", "description": "实验名（用于 run_id 前缀）"},
            "script": {"type": "string", "description": "启动脚本（相对实验目录），如 scripts/run.sh"},
            "command": {"type": "string", "description": "直接指定启动命令（优先于 script）"},
            "template": {"type": "string", "description": "模板名（仅记录到 run.json）"},
            "dir": {"type": "string", "description": "实验目录（默认为项目根目录）"},
            "sync": {"type": "boolean", "default": True, "description": "远端启动前是否 rsync 上传项目"},
            "local": {"type": "boolean", "default": False, "description": "无服务器时本地后台启动"},
            "smoke": {"type": "boolean", "default": False, "description": "运行 scripts/smoke.sh（CPU 无数据自检）"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            record = launch_experiment(
                ctx,
                server=kwargs.get("server"),
                name=kwargs.get("name"),
                script=kwargs.get("script"),
                command=kwargs.get("command"),
                template=kwargs.get("template"),
                exp_dir=kwargs.get("dir"),
                sync=bool(kwargs.get("sync", True)),
                local=bool(kwargs.get("local")),
                smoke=bool(kwargs.get("smoke")),
            )
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            return ToolResult.failure(str(exc))
        content = describe_launch(record)
        data = record.to_dict()
        artifacts = [str(Path(record.run_dir) / "run.json")] if record.run_dir else []
        if record.status == "failed":
            return ToolResult(
                ok=False,
                content=content,
                data=data,
                artifacts=artifacts,
                error=record.error or "启动失败",
            )
        return ToolResult.success(content, data=data, artifacts=artifacts)


class ExpStatusTool(BaseTool):
    name = "exp_status"
    description = (
        "查看实验状态：不带 run_id 时列出全部 run；带 run_id 时读取 runs/<run_id>/run.json，"
        "并在可能时刷新远端进程存活/日志推断（running→done/failed）。"
    )
    tags = ("experiment",)
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "run id（省略则列出最近 runs）"},
            "refresh": {"type": "boolean", "default": True, "description": "是否探测远端进程/日志刷新状态"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        project_dir = Path(ctx.project_dir).resolve()
        run_id = str(kwargs.get("run_id") or "").strip()
        if not run_id:
            records = list_runs(project_dir, limit=int(kwargs.get("limit") or 20))
            if not records:
                return ToolResult.success(
                    "还没有实验记录。先生成骨架：`sciforge exp scaffold --template generic --name my-exp`，"
                    "再 `sciforge exp launch --local`。",
                    data={"runs": []},
                )
            lines = ["| run_id | server | template | status | started_at |", "| --- | --- | --- | --- | --- |"]
            for record in records:
                lines.append(
                    f"| {record.run_id} | {record.server} | {record.template} | {record.status} | {record.started_at} |"
                )
            return ToolResult.success("\n".join(lines), data={"runs": [r.to_dict() for r in records]})
        record = load_run(project_dir, run_id)
        if record is None:
            return ToolResult.failure(f"未找到 run：{run_id}（在 {runs_root(project_dir)} 下）")
        if bool(kwargs.get("refresh", True)):
            record = refresh_record(ctx, record)
        lines = [
            f"run_id: {record.run_id}",
            f"status: {record.status}",
            f"server: {record.server} | template: {record.template}",
            f"started_at: {record.started_at} | updated_at: {record.updated_at}",
            f"git_commit: {record.git_commit or '(非 git 仓库或无提交)'}",
            f"command: {record.command}",
            f"log_path: {record.log_path}",
            f"job_id: {record.job_id or '-'}",
        ]
        if record.metrics:
            lines.append("metrics: " + json.dumps(record.metrics, ensure_ascii=False))
        if record.error:
            lines.append(f"error: {record.error}")
        return ToolResult.success("\n".join(lines), data=record.to_dict())


class ExpLogsTool(BaseTool):
    name = "exp_logs"
    description = "查看实验训练日志（本地或 SSH tail）；用于监控训练进度。"
    tags = ("experiment", "remote")
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "run id（省略则取最近一次）"},
            "lines": {"type": "integer", "default": 100},
            "log_path": {"type": "string", "description": "直接指定日志路径（覆盖 run.json 记录）"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        project_dir = Path(ctx.project_dir).resolve()
        run_id = str(kwargs.get("run_id") or "").strip()
        record = load_run(project_dir, run_id) if run_id else latest_run(project_dir)
        if record is None:
            return ToolResult.failure("没有实验记录；先 `sciforge exp launch ...`。")
        log_path = str(kwargs.get("log_path") or record.log_path or "")
        if not log_path:
            return ToolResult.failure(f"{record.run_id} 没有记录日志路径")
        executor, _server = _executor_for_record(ctx, record)
        if executor is None:
            return ToolResult.failure(
                f"服务器 {record.server} 不在配置中；无法读取远端日志。可直接 ssh 查看 {log_path}"
            )
        result = executor.tail_logs(log_path, lines=int(kwargs.get("lines") or 100))
        if not result.ok:
            return ToolResult.failure(
                f"读取日志失败：{(result.stderr or result.stdout).strip()[:500]}",
                content=f"日志路径：{log_path}",
            )
        inferred = infer_status_from_log(result.stdout)
        content = (
            f"[{record.run_id}] {log_path}\n{result.stdout.strip() or '（日志为空）'}"
            + (f"\n\n推断状态：{inferred}" if inferred else "")
        )
        return ToolResult.success(
            content,
            data={"run_id": record.run_id, "log_path": log_path, "inferred_status": inferred},
        )


class ExpStopTool(BaseTool):
    name = "exp_stop"
    description = "停止实验（tmux 窗口/PID/Slurm scancel），并把 run.json 状态改为 stopped。破坏性操作。"
    tags = ("experiment", "remote")
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "run id（省略则取最近 running 的实验）"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        project_dir = Path(ctx.project_dir).resolve()
        run_id = str(kwargs.get("run_id") or "").strip()
        record = load_run(project_dir, run_id) if run_id else latest_run(project_dir, status="running")
        if record is None:
            return ToolResult.failure("未找到可停止的实验记录。")
        if not record.job_id:
            return ToolResult.failure(f"{record.run_id} 没有记录 job_id，无法自动停止；可手动登录服务器 kill。")
        executor, _server = _executor_for_record(ctx, record)
        if executor is None:
            return ToolResult.failure(f"服务器 {record.server} 不在配置中，无法自动停止。")
        result = executor.kill_job(record.job_id)
        if getattr(result, "ok", False):
            if record.status not in TERMINAL_STATUSES:
                try:
                    record.transition("stopped")
                except ValueError:
                    record.status = "stopped"
                    record.updated_at = now_utc8()
                save_run(record, project_dir)
            return ToolResult.success(
                f"已停止 {record.run_id}（job {record.job_id}）",
                data=record.to_dict(),
            )
        return ToolResult.failure(
            f"停止失败：{(getattr(result, 'stderr', '') or getattr(result, 'stdout', '')).strip()[:500]}",
            content=f"可手动执行：ssh {record.server} 'kill {record.job_id}'",
        )


class ExpResultsTool(BaseTool):
    name = "exp_results"
    description = (
        "收集实验结果：找 run 的 metrics.json（本地或从远端下载）→ 写 "
        "results/<run_id>/{metrics.json, table.md 三线表, curves.json 曲线数据}，"
        "并把摘要写回 run.json。"
    )
    tags = ("experiment",)
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "run id（省略则取最近一次）"},
            "metrics": {"type": "string", "description": "直接指定 metrics.json 路径"},
            "download": {"type": "boolean", "default": True, "description": "远端 run 是否自动下载 metrics"},
            "out": {"type": "string", "description": "结果根目录（默认项目 results/）"},
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            info = collect_results(
                ctx,
                run_id=kwargs.get("run_id"),
                metrics_path=kwargs.get("metrics"),
                download=bool(kwargs.get("download", True)),
                out_dir=kwargs.get("out"),
            )
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            return ToolResult.failure(str(exc))
        content = (
            f"结果已汇总：{info['run_id']}（{info['num_epochs']} 个 epoch）\n"
            f"来源: {info['source']}\n\n{info['table']}\n\n"
            f"产出：\n  - {info['metrics_path']}\n  - {info['table_path']}\n  - {info['curves_path']}"
        )
        return ToolResult.success(
            content,
            data={key: info[key] for key in ("run_id", "source", "result_dir", "num_epochs", "summary")},
            artifacts=[info["metrics_path"], info["table_path"], info["curves_path"]],
        )


class ExpSweepTool(BaseTool):
    name = "exp_sweep"
    description = (
        "参数扫描：读取 sweep YAML（base + grid 参数网格）→ 为每个组合生成 runs/<run_id>/run.json；"
        "launch=true 时按 server.max_parallel_jobs 上限提交前 N 个。"
    )
    tags = ("experiment", "remote")
    parameters = {
        "type": "object",
        "properties": {
            "config": {"type": "string", "description": "sweep YAML 路径（相对项目目录）"},
            "server": {"type": "string", "description": "GPU 服务器名"},
            "launch": {"type": "boolean", "default": False, "description": "是否真正提交实验"},
            "max_parallel": {"type": "integer", "description": "覆盖 server.max_parallel_jobs"},
            "sync": {"type": "boolean", "default": True},
            "local": {"type": "boolean", "default": False},
        },
        "required": ["config"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            info = sweep_experiments(
                ctx,
                config_path=self.require(kwargs, "config"),
                server=kwargs.get("server"),
                launch=bool(kwargs.get("launch")),
                max_parallel=kwargs.get("max_parallel"),
                sync=bool(kwargs.get("sync", True)),
                local=bool(kwargs.get("local")),
            )
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            return ToolResult.failure(str(exc))
        artifacts = [
            str(run_dir_for(Path(ctx.project_dir), record["run_id"]) / "run.json")
            for record in info["runs"]
        ]
        return ToolResult.success(describe_sweep(info), data=info, artifacts=artifacts)


register_tool(ExpScaffoldTool())
register_tool(ExpLaunchTool())
register_tool(ExpStatusTool())
register_tool(ExpLogsTool())
register_tool(ExpStopTool())
register_tool(ExpResultsTool())
register_tool(ExpSweepTool())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _make_ctx(project_dir: Path | None = None) -> ToolContext:
    from sciforge.config import data_home, find_project_root, get_settings

    project = (
        Path(project_dir).expanduser().resolve() if project_dir else (find_project_root() or Path.cwd())
    )
    return ToolContext(
        settings=get_settings(project),
        project_dir=project,
        data_dir=data_home(),
        session_dir=project / ".sciforge" / "sessions" / "cli",
    )


def _json_out(payload: Any) -> None:
    import typer

    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _console():
    from rich.console import Console

    return Console()


def _fail(message: str, *, json_mode: bool = False):
    import typer

    if json_mode:
        _json_out({"ok": False, "error": message})
    else:
        _console().print(f"[red]✗ {message}[/red]")
    raise typer.Exit(code=1)


def _run_tool_or_fail(tool: BaseTool, ctx: ToolContext, *, json_mode: bool, **kwargs: Any) -> ToolResult:
    import typer

    try:
        result = tool.run(ctx, **kwargs)
    except ValueError as exc:  # 参数缺失等可预期错误，不裸抛 traceback
        result = ToolResult.failure(str(exc))
    if json_mode:
        _json_out({"ok": result.ok, **result.data, "content": result.content, "artifacts": result.artifacts})
        if not result.ok:
            raise typer.Exit(1)
        return result
    if result.ok:
        _console().print(result.content)
    else:
        _fail(result.error or result.content, json_mode=False)
    return result


def build_exp_app() -> Any:
    import typer

    app = typer.Typer(
        help="实验管理：scaffold / launch / status / logs / stop / results / sweep / list",
        no_args_is_help=True,
    )

    @app.command("scaffold")
    def exp_scaffold(
        template: str = typer.Option("generic", "-t", "--template", help="cv-classification|nlp-finetune|llm-sft|generic"),
        name: str = typer.Option("my-exp", "-n", "--name", help="实验名"),
        dataset: str = typer.Option(None, "-d", "--dataset"),
        model: str = typer.Option(None, "-m", "--model"),
        dest: str = typer.Option(None, "--dir", help="生成目录（默认 experiments/<name>）"),
        force: bool = typer.Option(False, "--force"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """按模板生成可训练的实验项目骨架。"""
        ctx = _make_ctx()
        _run_tool_or_fail(
            ExpScaffoldTool(),
            ctx,
            json_mode=json_mode,
            template=template,
            name=name,
            dataset=dataset,
            model=model,
            dest=dest,
            force=force,
        )

    @app.command("launch")
    def exp_launch(
        server: str = typer.Option(None, "--server", help="GPU 服务器名"),
        name: str = typer.Option(None, "--name"),
        script: str = typer.Option(None, "--script", help="启动脚本，如 scripts/run.sh"),
        command: str = typer.Option(None, "--command", help="直接指定启动命令"),
        template: str = typer.Option(None, "--template"),
        directory: str = typer.Option(None, "--dir", help="实验目录（默认项目根）"),
        sync: bool = typer.Option(True, "--sync/--no-sync", help="远端启动前 rsync 上传"),
        local: bool = typer.Option(False, "--local", help="无服务器时本地后台启动"),
        smoke: bool = typer.Option(False, "--smoke", help="运行 scripts/smoke.sh（CPU 无数据自检）"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """启动实验：校验配置 → 同步 → 后台运行 → 写 run.json。"""
        ctx = _make_ctx()
        _run_tool_or_fail(
            ExpLaunchTool(),
            ctx,
            json_mode=json_mode,
            server=server,
            name=name,
            script=script,
            command=command,
            template=template,
            dir=directory,
            sync=sync,
            local=local,
            smoke=smoke,
        )

    @app.command("status")
    def exp_status(
        run_id: str = typer.Argument(None, help="run id（省略则列出全部）"),
        refresh: bool = typer.Option(True, "--refresh/--no-refresh"),
        limit: int = typer.Option(20, "--limit"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """查看实验状态。"""
        ctx = _make_ctx()
        _run_tool_or_fail(
            ExpStatusTool(), ctx, json_mode=json_mode, run_id=run_id, refresh=refresh, limit=limit
        )

    @app.command("logs")
    def exp_logs(
        run_id: str = typer.Argument(None, help="run id（省略则取最近一次）"),
        lines: int = typer.Option(100, "--lines", "--tail", help="最后 N 行（--tail 为别名）"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """查看训练日志。"""
        ctx = _make_ctx()
        _run_tool_or_fail(ExpLogsTool(), ctx, json_mode=json_mode, run_id=run_id, lines=lines)

    @app.command("stop")
    def exp_stop(
        run_id: str = typer.Argument(None, help="run id（省略则取最近 running）"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """停止实验。"""
        ctx = _make_ctx()
        _run_tool_or_fail(ExpStopTool(), ctx, json_mode=json_mode, run_id=run_id)

    @app.command("results")
    def exp_results(
        run_ids: list[str] = typer.Argument(None, help="run id 列表（省略则取最近一次）"),
        metrics: str = typer.Option(None, "--metrics", help="直接指定 metrics.json"),
        download: bool = typer.Option(True, "--download/--no-download"),
        out: str = typer.Option(None, "--out", help="结果根目录（默认 results/）"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """汇总结果：metrics.json → 三线表 + 曲线数据（支持一次多个 run）。"""
        ctx = _make_ctx()
        targets: list[str | None] = list(run_ids or []) or [None]
        results = []
        for target in targets:
            tool_result = ExpResultsTool().run(
                ctx, run_id=target, metrics=metrics, download=download, out=out
            )
            results.append(tool_result)
        if json_mode:
            if len(results) == 1:
                payload = {
                    "ok": results[0].ok,
                    **results[0].data,
                    "content": results[0].content,
                    "artifacts": results[0].artifacts,
                }
            else:
                payload = {
                    "ok": all(result.ok for result in results),
                    "runs": [
                        {"ok": result.ok, **result.data, "content": result.content, "artifacts": result.artifacts}
                        for result in results
                    ],
                }
            _json_out(payload)
            if not all(result.ok for result in results):
                raise typer.Exit(1)
            return
        summary_lines: list[str] = []
        for result in results:
            if result.ok:
                _console().print(result.content)
                summary_lines.append(result.content)
            else:
                _fail(result.error or result.content, json_mode=False)
        if len(results) > 1:
            from rich.table import Table

            table = Table(title="结果汇总")
            table.add_column("run_id")
            table.add_column("epochs")
            table.add_column("summary")
            for result in results:
                table.add_row(
                    str(result.data.get("run_id", "")),
                    str(result.data.get("num_epochs", "")),
                    json.dumps(result.data.get("summary", {}), ensure_ascii=False),
                )
            _console().print(table)

    @app.command("sweep")
    def exp_sweep(
        config: str = typer.Argument(..., help="sweep YAML 路径"),
        server: str = typer.Option(None, "--server"),
        launch: bool = typer.Option(False, "--launch", help="真正提交实验"),
        max_parallel: int = typer.Option(None, "--max-parallel"),
        sync: bool = typer.Option(True, "--sync/--no-sync"),
        local: bool = typer.Option(False, "--local"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """参数扫描：生成/启动一组实验。"""
        ctx = _make_ctx()
        _run_tool_or_fail(
            ExpSweepTool(),
            ctx,
            json_mode=json_mode,
            config=config,
            server=server,
            launch=launch,
            max_parallel=max_parallel,
            sync=sync,
            local=local,
        )

    @app.command("list")
    def exp_list(
        limit: int = typer.Option(50, "--limit"),
        status: str = typer.Option(None, "--status", help="过滤状态：pending|running|done|failed|stopped"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """列出实验运行记录。"""
        ctx = _make_ctx()
        records = list_runs(ctx.project_dir, limit=limit, status=status)
        if json_mode:
            _json_out({"runs": [record.to_dict() for record in records]})
            return
        if not records:
            _console().print("还没有实验记录。")
            return
        from rich.table import Table

        table = Table(title="实验运行记录")
        for column in ("run_id", "server", "template", "status", "started_at", "git_commit"):
            table.add_column(column)
        for record in records:
            table.add_row(
                record.run_id,
                record.server,
                record.template,
                record.status,
                record.started_at,
                record.git_commit or "-",
            )
        _console().print(table)

    @app.command("templates")
    def exp_templates(json_mode: bool = typer.Option(False, "--json")) -> None:
        """列出内置实验模板。"""
        templates = list_templates()
        if json_mode:
            _json_out({"templates": templates})
            return
        from rich.table import Table

        table = Table(title="实验模板")
        for column in ("name", "description", "default_dataset", "default_model"):
            table.add_column(column)
        for meta in templates:
            table.add_row(
                str(meta.get("name", "")),
                str(meta.get("description", "")),
                str(meta.get("default_dataset", "")),
                str(meta.get("default_model", "")),
            )
        _console().print(table)

    return app


def add_cli(app: Any) -> None:
    """把 `exp` 子命令挂到根 Typer app；并尽力补挂 ssh/gpu（保持两种调用方式都可用）。"""
    from sciforge.tools.remote import _add_typer_once

    _add_typer_once(app, "exp", build_exp_app)
    try:
        from sciforge.tools.remote import add_cli as _add_remote_cli

        _add_remote_cli(app)
    except Exception:  # noqa: BLE001 - 远程模块不可用时不影响 exp
        pass


__all__ = [
    "RunRecord",
    "RUN_STATUSES",
    "TERMINAL_STATUSES",
    "ALLOWED_TRANSITIONS",
    "scaffold_experiment",
    "list_templates",
    "get_template_meta",
    "launch_experiment",
    "collect_results",
    "sweep_experiments",
    "expand_grid",
    "normalize_metrics",
    "render_markdown_table",
    "build_curve_data",
    "infer_status_from_log",
    "run_dir_for",
    "results_root",
    "runs_root",
    "list_runs",
    "latest_run",
    "load_run",
    "save_run",
    "new_run_id",
    "git_info",
    "add_cli",
]
