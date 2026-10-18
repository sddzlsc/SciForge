"""T2 远程执行层测试：命令构造、解析器、工具行为、CLI 挂载。

全部离线：``SSHExecutor`` 的执行通过注入 ``runner`` 或 monkeypatch
:func:`sciforge.tools.remote._run_process` 模拟，绝不真实连接 SSH。
"""

from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from sciforge.config import ServerConfig, Settings
from sciforge.core.registry import ToolContext
from sciforge.tools import remote
from sciforge.tools.remote import LocalExecutor, RemoteResult, SSHExecutor

# ---------------------------------------------------------------------------
# 样例数据
# ---------------------------------------------------------------------------

NVIDIA_SMI_SAMPLE = """0, NVIDIA GeForce RTX 4090, 24564, 1234, 35, 52
1, NVIDIA A100-SXM4-80GB, 81920, 45000, 92, 71
"""

NVIDIA_APPS_SAMPLE = """12345, python, 1234
678, /usr/bin/python3, 8000
"""

SQUEUE_SAMPLE = """JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
12345      gpu     exp1    alice  R    1:23:45      1 gpu001
12346      gpu     exp2      bob PD       0:00      1 (Priority)
"""

SACCT_SAMPLE = """12345|exp1|gpu|COMPLETED|0:0|1:23:45|2026-10-18T10:00:00|2026-10-18T11:23:45
12345.batch|batch||COMPLETED|0:0|1:23:40|2026-10-18T10:00:05|2026-10-18T11:23:45
12346|exp2|gpu|FAILED|1:0|0:10:00|2026-10-18T10:00:00|2026-10-18T10:10:00
"""

TMUX_LS_SAMPLE = """sciforge: 2 windows (created Sun Oct 18 18:00:00 2026)
exp2: 1 windows (created Sun Oct 18 18:10:00 2026)
"""

CHECK_SAMPLE = """SCIFORGE_OS=Linux 5.15.0-1041-aws x86_64
SCIFORGE_HOST=gpu001
SCIFORGE_USER=root
SCIFORGE_PY=Python 3.10.12
SCIFORGE_PY_PATH=/opt/conda/envs/torch/bin/python
SCIFORGE_TORCH=2.4.1
SCIFORGE_CUDA=535.104.05
SCIFORGE_GPU_COUNT=2
SCIFORGE_GPU=NVIDIA A100-SXM4-80GB;NVIDIA A100-SXM4-80GB;
SCIFORGE_TMUX=tmux 3.3a
SCIFORGE_CONDA=conda 23.7.4
SCIFORGE_WORKDIR=/root
"""


# ---------------------------------------------------------------------------
# fixtures & helpers
# ---------------------------------------------------------------------------


class FakeRunner:
    """记录 argv 并按 responder 返回结果的假进程执行器。"""

    def __init__(self, responder=None):
        self.calls: list[list[str]] = []
        self.responder = responder or (lambda argv, timeout: RemoteResult(0, "ok"))

    def __call__(self, argv, *, timeout=60, cwd=None, env=None, input_text=None):
        self.calls.append([str(item) for item in argv])
        return self.responder([str(item) for item in argv], timeout)

    @property
    def last(self) -> list[str]:
        return self.calls[-1]

    @property
    def joined(self) -> str:
        return "\n---\n".join(" ".join(call) for call in self.calls)


@pytest.fixture
def server() -> ServerConfig:
    return ServerConfig(
        name="gpu1",
        host="10.0.0.2",
        port=2222,
        user="root",
        identity_file="~/.ssh/id_ed25519",
        proxy_jump="jump@1.2.3.4",
        workdir="~/sciforge",
        conda_env="torch",
        tmux_session="sciforge",
        scheduler="none",
    )


@pytest.fixture
def executor(server: ServerConfig) -> SSHExecutor:
    return SSHExecutor(server)


@pytest.fixture
def ctx(tmp_path, server: ServerConfig) -> ToolContext:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return ToolContext(
        settings=Settings(servers=[server]),
        project_dir=project,
        data_dir=data_dir,
        session_dir=project / ".sciforge" / "sessions" / "t",
    )


@pytest.fixture
def ctx_no_server(tmp_path) -> ToolContext:
    project = tmp_path / "project-noserver"
    project.mkdir()
    data_dir = tmp_path / "data-noserver"
    data_dir.mkdir()
    return ToolContext(
        settings=Settings(servers=[]),
        project_dir=project,
        data_dir=data_dir,
        session_dir=project / ".sciforge" / "sessions" / "t",
    )


# ---------------------------------------------------------------------------
# 命令构造与安全
# ---------------------------------------------------------------------------


def test_ssh_argv_contains_options_and_target(executor: SSHExecutor) -> None:
    argv = executor.ssh_argv("echo hi")
    assert argv[0] == "ssh"
    joined = " ".join(argv)
    assert "-o BatchMode=yes" in joined
    assert "-o ConnectTimeout=10" in joined
    assert "-i" in argv and str(argv[argv.index("-i") + 1]).endswith("id_ed25519")
    assert "-p" in argv and argv[argv.index("-p") + 1] == "2222"
    assert "-J" in argv and argv[argv.index("-J") + 1] == "jump@1.2.3.4"
    assert argv[-2] == "root@10.0.0.2"
    assert argv[-1] == "echo hi"


def test_ssh_argv_default_port_omitted() -> None:
    executor = SSHExecutor(ServerConfig(name="s", host="h", user="u"))
    argv = executor.ssh_argv("true")
    assert "-p" not in argv
    assert argv[-2] == "u@h"


def test_wrap_remote_command_all_pieces() -> None:
    wrapped = remote.wrap_remote_command(
        "python train.py",
        workdir="~/sciforge",
        conda_env="torch",
        env={"CUDA_VISIBLE_DEVICES": "0"},
    )
    assert wrapped.startswith("bash -lc ")
    assert "set -euo pipefail" in wrapped
    assert "cd ~/sciforge" in wrapped
    assert "conda activate torch" in wrapped
    assert "export CUDA_VISIBLE_DEVICES=0" in wrapped
    assert "python train.py" in wrapped


def test_wrap_remote_command_quotes_workdir() -> None:
    wrapped = remote.wrap_remote_command("ls", workdir="/data/my project")
    assert "/data/my project" in wrapped
    script = wrapped.removeprefix("bash -lc ")
    assert script.startswith("'") and script.endswith("'")


def test_conda_activate_script_contains_activation() -> None:
    script = remote.conda_activate_script("torch")
    assert "conda shell.bash hook" in script
    assert "conda activate torch" in script


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "sudo rm -rf / --no-preserve-root",
        "mkfs.ext4 /dev/sda1",
        "shutdown -h now",
        "reboot",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "chmod -R 777 /",
    ],
)
def test_dangerous_commands_rejected(command: str) -> None:
    reason = remote.is_dangerous_command(command)
    assert reason, f"应识别危险命令: {command}"
    with pytest.raises(PermissionError):
        remote.ensure_safe_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "nvidia-smi",
        "python train.py --epochs 10",
        "rm -rf ./outputs/cache",
        "git status",
        "sbatch train.sbatch",
    ],
)
def test_safe_commands_allowed(command: str) -> None:
    assert remote.is_dangerous_command(command) is None
    remote.ensure_safe_command(command)


def test_resolve_remote_path() -> None:
    assert remote.resolve_remote_path("runs/1", "~/sciforge")[0] == "~/sciforge/runs/1"
    absolute, warning = remote.resolve_remote_path("~/sciforge/data", "~/sciforge")
    assert absolute == "~/sciforge/data"
    assert warning is None
    outside, warning = remote.resolve_remote_path("/tmp/scratch", "~/sciforge")
    assert outside == "/tmp/scratch"
    assert warning and "不在工作目录" in warning
    escape, warning = remote.resolve_remote_path("../secrets", "~/sciforge")
    assert warning is not None
    with pytest.raises(PermissionError):
        remote.resolve_remote_path("/etc/passwd", "~/sciforge", allow_outside=False)


# ---------------------------------------------------------------------------
# 纯解析器
# ---------------------------------------------------------------------------


def test_parse_nvidia_smi() -> None:
    gpus = remote.parse_nvidia_smi(NVIDIA_SMI_SAMPLE)
    assert len(gpus) == 2
    assert gpus[0]["index"] == 0
    assert gpus[0]["name"] == "NVIDIA GeForce RTX 4090"
    assert gpus[0]["memory_total_mb"] == 24564
    assert gpus[0]["memory_used_mb"] == 1234
    assert gpus[0]["memory_free_mb"] == 23330
    assert gpus[0]["utilization_percent"] == 35
    assert gpus[0]["temperature_c"] == 52
    assert gpus[0]["memory_used_percent"] == 5.0
    assert gpus[1]["name"] == "NVIDIA A100-SXM4-80GB"


def test_parse_nvidia_smi_handles_na_and_header() -> None:
    text = """index, name, memory.total [MiB], memory.used [MiB], utilization.gpu [%], temperature.gpu
0, Tesla T4, 15360, [N/A], 10, [N/A]
"""
    gpus = remote.parse_nvidia_smi(text)
    assert len(gpus) == 1
    assert gpus[0]["memory_used_mb"] is None
    assert gpus[0]["utilization_percent"] == 10
    assert gpus[0]["temperature_c"] is None


def test_parse_nvidia_smi_apps() -> None:
    apps = remote.parse_nvidia_smi_apps(NVIDIA_APPS_SAMPLE)
    assert len(apps) == 2
    assert apps[0] == {"pid": 12345, "process_name": "python", "used_memory_mb": 1234}
    assert apps[1]["process_name"] == "/usr/bin/python3"


def test_parse_squeue() -> None:
    jobs = remote.parse_squeue(SQUEUE_SAMPLE)
    assert len(jobs) == 2
    assert jobs[0]["job_id"] == "12345"
    assert jobs[0]["partition"] == "gpu"
    assert jobs[0]["name"] == "exp1"
    assert jobs[0]["user"] == "alice"
    assert jobs[0]["state"] == "R"
    assert jobs[0]["time"] == "1:23:45"
    assert jobs[0]["nodelist"] == "gpu001"
    assert jobs[1]["state"] == "PD"


def test_parse_sacct_skips_batch_entries() -> None:
    jobs = remote.parse_sacct(SACCT_SAMPLE)
    assert [job["job_id"] for job in jobs] == ["12345", "12346"]
    assert jobs[0]["state"] == "COMPLETED"
    assert jobs[1]["state"] == "FAILED"
    assert jobs[0]["exit_code"] == "0:0"


def test_parse_tmux_sessions() -> None:
    sessions = remote.parse_tmux_sessions(TMUX_LS_SAMPLE)
    assert sessions[0] == {"name": "sciforge", "windows": 2}
    assert sessions[1] == {"name": "exp2", "windows": 1}


def test_parse_check_output() -> None:
    info = remote.parse_check_output(CHECK_SAMPLE, rc=0)
    assert info["os"].startswith("Linux")
    assert info["user"] == "root"
    assert info["torch"] == "2.4.1"
    assert info["cuda"] == "535.104.05"
    assert info["gpu_count"] == 2
    assert info["gpu_names"] == ["NVIDIA A100-SXM4-80GB", "NVIDIA A100-SXM4-80GB"]
    assert info["tmux"] == "tmux 3.3a"
    assert info["conda"] == "conda 23.7.4"
    assert info["workdir"] == "/root"


def test_parse_sbatch_output() -> None:
    assert remote.parse_sbatch_output("Submitted batch job 12345") == 12345
    assert remote.parse_sbatch_output("12345\n") == 12345
    assert remote.parse_sbatch_output("slurm_load_jobs error") is None


def test_format_gpu_table() -> None:
    table = remote.format_gpu_table(remote.parse_nvidia_smi(NVIDIA_SMI_SAMPLE), title="gpu1")
    assert "### gpu1" in table
    assert "RTX 4090" in table
    assert "24.0 GB" in table or "1.2 / 24.0" in table


# ---------------------------------------------------------------------------
# SSHExecutor 执行
# ---------------------------------------------------------------------------


def test_run_wraps_and_returns_tuple(executor: SSHExecutor) -> None:
    runner = FakeRunner(lambda argv, timeout: RemoteResult(0, "GPU 0 ok\n"))
    executor = SSHExecutor(executor.server, runner=runner)
    rc, stdout, stderr = executor.run("nvidia-smi")
    assert (rc, stdout, stderr) == (0, "GPU 0 ok\n", "")
    argv = runner.last
    assert argv[0] == "ssh"
    assert "nvidia-smi" in argv[-1]
    assert "set -euo pipefail" in argv[-1]
    assert "cd ~/sciforge" in argv[-1]
    assert "conda activate torch" in argv[-1]


def test_run_propagates_failure(executor: SSHExecutor) -> None:
    runner = FakeRunner(lambda argv, timeout: RemoteResult(255, "", "ssh: connect refused"))
    executor = SSHExecutor(executor.server, runner=runner)
    result = executor.run("true")
    assert result.rc == 255
    assert result.ok is False
    assert "connect refused" in result.stderr


def test_check_parses_and_creates_workdir(executor: SSHExecutor) -> None:
    def responder(argv, timeout):
        command = argv[-1]
        if "SCIFORGE_OS" in command:
            return RemoteResult(0, CHECK_SAMPLE)
        if command.startswith("mkdir -p"):
            return RemoteResult(0, "")
        return RemoteResult(0, "")

    runner = FakeRunner(responder)
    executor = SSHExecutor(executor.server, runner=runner)
    info = executor.check()
    assert info["reachable"] is True
    assert info["gpu_count"] == 2
    assert info["workdir_ready"] is True
    assert info["server"] == "gpu1"


def test_check_failure_never_raises(executor: SSHExecutor) -> None:
    runner = FakeRunner(lambda argv, timeout: RemoteResult(255, "", "Permission denied (publickey)"))
    executor = SSHExecutor(executor.server, runner=runner)
    info = executor.check()
    assert info["reachable"] is False
    assert "Permission denied" in info["error"]


def test_run_detached_uses_tmux(executor: SSHExecutor) -> None:
    def responder(argv, timeout):
        command = argv[-1]
        if "tmux has-session" in command:
            return RemoteResult(1, "", "no server running on /tmp/tmux")
        if "tmux new-session" in command:
            return RemoteResult(0, "")
        return RemoteResult(0, "")

    runner = FakeRunner(responder)
    executor = SSHExecutor(executor.server, runner=runner)
    job = executor.run_detached("run1", "python train.py", "~/sciforge/runs/run1/train.log")
    assert job["mode"] == "tmux"
    assert job["job_id"] == "sciforge:run1"
    assert job["log_path"].endswith("runs/run1/train.log")
    assert "tmux new-session -d" in runner.joined
    assert "exec >>" in runner.joined


def test_run_detached_falls_back_to_nohup(executor: SSHExecutor) -> None:
    def responder(argv, timeout):
        command = argv[-1]
        if "tmux has-session" in command:
            return RemoteResult(1, "", "no sessions")
        if "tmux new-session" in command:
            return RemoteResult(127, "", "tmux: command not found")
        if "nohup" in command:
            return RemoteResult(0, "4242\n")
        return RemoteResult(0, "")

    runner = FakeRunner(responder)
    executor = SSHExecutor(executor.server, runner=runner)
    job = executor.run_detached("run2", "python train.py")
    assert job["mode"] == "nohup"
    assert job["pid"] == 4242
    assert job["job_id"] == "4242"
    assert "tmux: command not found" in job["tmux_error"]


def test_upload_argv_rsync_with_excludes(monkeypatch, executor: SSHExecutor, tmp_path) -> None:
    monkeypatch.setattr(remote, "rsync_available", lambda: True)
    local = tmp_path / "configs"
    local.mkdir()
    argv = executor.build_upload_argv([local], "runs/", excludes=["data/", "*.log"])
    assert argv[0] == "rsync"
    assert "-az" in argv
    assert "--exclude=data/" in argv
    assert "--exclude=*.log" in argv
    assert "-e" in argv and argv[argv.index("-e") + 1].startswith("ssh ")
    assert "-p 2222" in argv[argv.index("-e") + 1]
    assert str(local) in argv
    assert argv[-1] == "root@10.0.0.2:~/sciforge/runs/"
    # 目录内容同步（末尾斜杠）也要保留
    argv2 = executor.build_upload_argv([str(local) + "/"], "runs")
    assert argv2[-2] == str(local) + "/"


def test_upload_argv_scp_fallback(monkeypatch, executor: SSHExecutor, tmp_path) -> None:
    monkeypatch.setattr(remote, "rsync_available", lambda: False)
    local = tmp_path / "a.txt"
    local.write_text("x")
    argv = executor.build_upload_argv([local], "data")
    assert argv[0] == "scp"
    assert "-P" in argv and argv[argv.index("-P") + 1] == "2222"
    assert argv[-1] == "root@10.0.0.2:~/sciforge/data"


def test_download_argv_rsync(monkeypatch, executor: SSHExecutor, tmp_path) -> None:
    monkeypatch.setattr(remote, "rsync_available", lambda: True)
    argv = executor.build_download_argv(["runs/abc1/metrics.json"], tmp_path / "out")
    assert argv[0] == "rsync"
    assert "root@10.0.0.2:~/sciforge/runs/abc1/metrics.json" in argv
    assert argv[-1] == str(tmp_path / "out") + "/"


def test_upload_paths_executes_rsync(monkeypatch, executor: SSHExecutor, tmp_path) -> None:
    monkeypatch.setattr(remote, "rsync_available", lambda: True)
    runner = FakeRunner(lambda argv, timeout: RemoteResult(0, "sent 12 bytes"))
    executor = SSHExecutor(executor.server, runner=runner)
    result = executor.upload_paths([tmp_path], "~/sciforge")
    assert result.ok
    assert runner.last[0] == "rsync"


def test_tail_logs_command(executor: SSHExecutor) -> None:
    runner = FakeRunner(lambda argv, timeout: RemoteResult(0, "line1\nline2\n"))
    executor = SSHExecutor(executor.server, runner=runner)
    result = executor.tail_logs("runs/abc/train.log", lines=42)
    assert result.ok
    command = runner.last[-1]
    assert "tail -n 42" in command
    assert "runs/abc/train.log" in command


def test_job_state_tmux_and_pid(executor: SSHExecutor) -> None:
    def responder(argv, timeout):
        command = argv[-1]
        if "list-windows" in command:
            return RemoteResult(0, "run1\nother\n")
        if "kill -0" in command:
            return RemoteResult(0, "")
        return RemoteResult(0, "")

    runner = FakeRunner(responder)
    executor = SSHExecutor(executor.server, runner=runner)
    assert executor.job_state("sciforge:run1")["alive"] is True
    assert executor.job_state("sciforge:missing")["alive"] is False
    assert executor.job_state("12345")["alive"] is True


def test_kill_job_variants(executor: SSHExecutor) -> None:
    runner = FakeRunner(lambda argv, timeout: RemoteResult(0, ""))
    executor = SSHExecutor(executor.server, runner=runner)
    executor.kill_job("sciforge:run1", kind="tmux")
    assert "tmux kill-window -t sciforge:run1" in runner.last[-1]
    executor.kill_job("sciforge", kind="tmux")
    assert "tmux kill-session -t sciforge" in runner.last[-1]
    executor.kill_job("12345", kind="slurm")
    assert runner.last[-1].startswith("scancel 12345")
    executor.kill_job("999", kind="pid")
    assert runner.last[-1] == "kill 999"


def test_render_sbatch_script() -> None:
    script = remote.render_sbatch_script(
        job_name="sft run",
        command="python -m src.train",
        workdir="~/sciforge",
        conda_env="torch",
        partition="gpu",
        gpu_type="a100",
        gpus=2,
        cpus=8,
        mem="64G",
        time="12:00:00",
        log_path="~/sciforge/slurm-%j.log",
    )
    assert script.startswith("#!/usr/bin/env bash")
    assert "#SBATCH --job-name=sft-run" in script
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --gres=gpu:a100:2" in script
    assert "#SBATCH --cpus-per-task=8" in script
    assert "#SBATCH --mem=64G" in script
    assert "#SBATCH --time=12:00:00" in script
    assert "conda activate torch" in script
    assert "cd ~/sciforge" in script
    assert "python -m src.train" in script


# ---------------------------------------------------------------------------
# 本地 GPU 降级
# ---------------------------------------------------------------------------


def test_local_gpu_info_darwin(monkeypatch) -> None:
    monkeypatch.setattr(remote.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(remote.platform, "machine", lambda: "arm64")
    info = remote.local_gpu_info()
    assert info["available"] is False
    assert info["mps"] is True
    assert "Apple Silicon" in info["reason"]


def test_local_gpu_info_linux_with_gpu(monkeypatch) -> None:
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    monkeypatch.setattr(remote.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    runner = FakeRunner(lambda argv, timeout: RemoteResult(0, NVIDIA_SMI_SAMPLE))
    info = remote.local_gpu_info(runner=runner)
    assert info["available"] is True
    assert len(info["gpus"]) == 2
    assert info["gpus"][0]["name"] == "NVIDIA GeForce RTX 4090"


def test_local_gpu_info_linux_without_nvidia_smi(monkeypatch) -> None:
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    monkeypatch.setattr(remote.shutil, "which", lambda name: None)
    info = remote.local_gpu_info()
    assert info["available"] is False
    assert "nvidia-smi" in info["reason"]


# ---------------------------------------------------------------------------
# 工具行为（monkeypatch 执行器方法，绝不联网）
# ---------------------------------------------------------------------------


def test_gpu_status_tool_local_fallback(monkeypatch, ctx_no_server: ToolContext) -> None:
    monkeypatch.setattr(remote.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(remote.platform, "machine", lambda: "arm64")
    result = remote.GpuStatusTool().run(ctx_no_server)
    assert result.ok is True
    assert result.data["available"] is False
    assert "Apple Silicon" in result.content


def test_gpu_status_tool_remote(monkeypatch, ctx: ToolContext) -> None:
    def fake_run(self, command, *, timeout=60, wrap=True, workdir=None):
        if "query-compute-apps" in command:
            return RemoteResult(0, NVIDIA_APPS_SAMPLE)
        return RemoteResult(0, NVIDIA_SMI_SAMPLE)

    monkeypatch.setattr(remote.SSHExecutor, "run", fake_run)
    result = remote.GpuStatusTool().run(ctx, server="gpu1")
    assert result.ok is True
    server_data = result.data["servers"][0]
    assert len(server_data["gpus"]) == 2
    assert len(server_data["processes"]) == 2
    assert "RTX 4090" in result.content
    assert "pid 12345" in result.content


def test_gpu_status_tool_remote_failure(monkeypatch, ctx: ToolContext) -> None:
    monkeypatch.setattr(
        remote.SSHExecutor,
        "run",
        lambda self, command, **kw: RemoteResult(255, "", "ssh: connect timed out"),
    )
    result = remote.GpuStatusTool().run(ctx, server="gpu1")
    assert result.ok is False
    assert "无法获取 GPU 状态" in result.content


def test_gpu_status_tool_local_flag(monkeypatch, ctx: ToolContext) -> None:
    monkeypatch.setattr(remote.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(remote.platform, "machine", lambda: "x86_64")
    result = remote.GpuStatusTool().run(ctx, local=True)
    assert result.ok is True
    assert "mps" not in result.data or result.data.get("mps") is False


def test_remote_run_tool_short(monkeypatch, ctx: ToolContext) -> None:
    monkeypatch.setattr(
        remote.SSHExecutor,
        "run",
        lambda self, command, **kw: RemoteResult(0, "hello from gpu\n"),
    )
    result = remote.RemoteRunTool().run(ctx, command="python -c 'print(\"hi\")'")
    assert result.ok is True
    assert "hello from gpu" in result.content
    assert result.data["server"] == "gpu1"


def test_remote_run_tool_detached(monkeypatch, ctx: ToolContext) -> None:
    def fake_detached(self, name, command, log_path=None, **kwargs):
        return {
            "mode": "tmux",
            "name": name,
            "session": "sciforge",
            "window": name,
            "log_path": f"~/sciforge/runs/{name}/train.log",
            "job_id": f"sciforge:{name}",
            "command": command,
        }

    monkeypatch.setattr(remote.SSHExecutor, "run_detached", fake_detached)
    result = remote.RemoteRunTool().run(ctx, command="python train.py", detached=True, name="exp1")
    assert result.ok is True
    assert "sciforge:exp1" in result.content
    assert "remote_tail" in result.content
    assert result.data["mode"] == "tmux"


def test_remote_run_tool_rejects_dangerous(ctx: ToolContext) -> None:
    result = remote.RemoteRunTool().run(ctx, command="rm -rf /")
    assert result.ok is False
    assert "危险命令" in (result.error or "")


def test_remote_run_tool_no_server(ctx_no_server: ToolContext) -> None:
    result = remote.RemoteRunTool().run(ctx_no_server, command="nvidia-smi")
    assert result.ok is False
    assert "sciforge ssh add" in (result.error or "")


def test_remote_run_tool_unknown_server(ctx: ToolContext) -> None:
    result = remote.RemoteRunTool().run(ctx, command="ls", server="nope")
    assert result.ok is False
    assert "gpu1" in (result.error or "")


def test_remote_tail_tool(monkeypatch, ctx: ToolContext) -> None:
    monkeypatch.setattr(
        remote.SSHExecutor,
        "tail_logs",
        lambda self, path, lines=100, timeout=30: RemoteResult(0, "epoch 1 loss=0.5\n"),
    )
    result = remote.RemoteTailTool().run(ctx, log_path="runs/x/train.log", lines=10)
    assert result.ok is True
    assert "epoch 1" in result.content


def test_remote_sync_tool(monkeypatch, ctx: ToolContext) -> None:
    captured = {}

    def fake_upload(self, paths, remote_dir, *, delete=False, excludes=None, timeout=1800):
        captured.update(paths=[str(p) for p in paths], remote_dir=remote_dir, excludes=excludes)
        return RemoteResult(0, "sent 1 file")

    monkeypatch.setattr(remote.SSHExecutor, "upload_paths", fake_upload)
    local_dir = ctx.project_dir / "src"
    local_dir.mkdir()
    result = remote.RemoteSyncTool().run(ctx, paths=["src"], direction="upload", remote_dir="runs/")
    assert result.ok is True
    assert captured["remote_dir"] == "runs/"
    assert captured["paths"][0].endswith("src")


def test_remote_sync_tool_missing_local(ctx: ToolContext) -> None:
    result = remote.RemoteSyncTool().run(ctx, paths=["does-not-exist"], direction="upload")
    assert result.ok is False
    assert "不存在" in (result.error or "")


def test_remote_jobs_tool(monkeypatch, ctx: ToolContext) -> None:
    monkeypatch.setattr(
        remote.SSHExecutor,
        "list_tmux_sessions",
        lambda self, **kw: [{"name": "sciforge", "windows": 2}],
    )
    monkeypatch.setattr(
        remote.SSHExecutor, "list_windows", lambda self, session=None, **kw: ["run1", "run2"]
    )
    monkeypatch.setattr(remote.SSHExecutor, "slurm_queue", lambda self, **kw: [])
    result = remote.RemoteJobsTool().run(ctx)
    assert result.ok is True
    assert "sciforge(2w)" in result.content
    assert "run1" in result.content


def test_remote_kill_tool(monkeypatch, ctx: ToolContext) -> None:
    monkeypatch.setattr(
        remote.SSHExecutor, "kill_job", lambda self, job_id, kind="auto", timeout=30: RemoteResult(0, "killed")
    )
    result = remote.RemoteKillTool().run(ctx, job_id="sciforge:exp1")
    assert result.ok is True
    assert "已停止" in result.content


def test_slurm_submit_tool(monkeypatch, tmp_path) -> None:
    slurm_server = ServerConfig(
        name="cluster",
        host="10.0.0.3",
        user="me",
        workdir="/home/me/proj",
        scheduler="slurm",
        partition="gpu",
        gpu_type="a100",
        conda_env="torch",
    )
    project = tmp_path / "proj"
    project.mkdir()
    ctx = ToolContext(
        settings=Settings(servers=[slurm_server]),
        project_dir=project,
        data_dir=tmp_path / "data",
        session_dir=project / ".sciforge" / "sessions" / "t",
    )

    def fake_submit(self, script_text, *, run_id, log_path=None, timeout=60):
        assert "--gres=gpu:a100:1" in script_text
        assert "#SBATCH --partition=gpu" in script_text
        return {
            "job_id": "777",
            "script_path": f"/home/me/proj/runs/{run_id}/job.sbatch",
            "log_path": log_path or "slurm-%j.log",
            "command": script_text,
            "mode": "slurm",
        }

    monkeypatch.setattr(remote.SSHExecutor, "submit_slurm", fake_submit)
    result = remote.SlurmSubmitTool().run(ctx, command="bash scripts/run.sh", name="exp1")
    assert result.ok is True
    assert "777" in result.content
    assert result.data["job_id"] == "777"


def test_slurm_submit_tool_rejects_non_slurm(ctx: ToolContext) -> None:
    result = remote.SlurmSubmitTool().run(ctx, command="bash run.sh")
    assert result.ok is False
    assert "slurm" in (result.error or "").lower()


def test_slurm_status_tool(monkeypatch, tmp_path) -> None:
    slurm_server = ServerConfig(name="cluster", host="h", user="u", scheduler="slurm")
    project = tmp_path / "p"
    project.mkdir()
    ctx = ToolContext(
        settings=Settings(servers=[slurm_server]),
        project_dir=project,
        data_dir=tmp_path / "d",
        session_dir=project / ".sf" / "s",
    )
    monkeypatch.setattr(
        remote.SSHExecutor, "slurm_queue", lambda self, **kw: remote.parse_squeue(SQUEUE_SAMPLE)
    )
    monkeypatch.setattr(
        remote.SSHExecutor, "slurm_history", lambda self, jobs=None, **kw: remote.parse_sacct(SACCT_SAMPLE)
    )
    result = remote.SlurmStatusTool().run(ctx)
    assert result.ok is True
    assert "12345" in result.content
    assert "COMPLETED" in result.content


def test_slurm_logs_tool(monkeypatch, tmp_path) -> None:
    slurm_server = ServerConfig(name="cluster", host="h", user="u", scheduler="slurm")
    project = tmp_path / "p2"
    project.mkdir()
    ctx = ToolContext(
        settings=Settings(servers=[slurm_server]),
        project_dir=project,
        data_dir=tmp_path / "d2",
        session_dir=project / ".sf" / "s",
    )
    monkeypatch.setattr(
        remote.SSHExecutor,
        "tail_logs",
        lambda self, path, lines=100, timeout=30: RemoteResult(0, "epoch 2 done\n"),
    )
    monkeypatch.setattr(remote.SSHExecutor, "slurm_history", lambda self, jobs=None, **kw: [])
    result = remote.SlurmLogsTool().run(ctx, log_path="/home/u/runs/x/slurm-1.log", job_id="1")
    assert result.ok is True
    assert "epoch 2 done" in result.content


# ---------------------------------------------------------------------------
# LocalExecutor
# ---------------------------------------------------------------------------


def test_local_executor_run(tmp_path) -> None:
    executor = LocalExecutor(workdir=tmp_path)
    result = executor.run("echo hi && pwd")
    assert result.ok is True
    assert "hi" in result.stdout
    assert str(tmp_path) in result.stdout


def test_local_executor_run_detached(tmp_path) -> None:
    import time

    executor = LocalExecutor(workdir=tmp_path)
    log = tmp_path / "runs" / "j1" / "train.log"
    job = executor.run_detached("j1", "echo LOCAL_DONE", str(log))
    assert job["mode"] == "local-nohup"
    deadline = time.time() + 5
    while time.time() < deadline and "LOCAL_DONE" not in log.read_text(errors="replace"):
        time.sleep(0.05)
    assert "LOCAL_DONE" in log.read_text(errors="replace")
    state = executor.job_state(str(job["pid"]))
    assert state["alive"] in (True, False)


# ---------------------------------------------------------------------------
# CLI 挂载
# ---------------------------------------------------------------------------


def test_cli_ssh_and_gpu_are_wired(monkeypatch, tmp_path) -> None:
    home = tmp_path / "sciforge-home"
    monkeypatch.setenv("SCIFORGE_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    app = typer.Typer()
    remote.add_cli(app)
    runner = CliRunner()

    listed = runner.invoke(app, ["ssh", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["servers"] == []

    added = runner.invoke(
        app,
        ["ssh", "add", "gpu9", "--host", "9.9.9.9", "--user", "root", "--json"],
    )
    assert added.exit_code == 0, added.output
    assert (home / "config.toml").exists()
    listed2 = runner.invoke(app, ["ssh", "list", "--json"])
    payload = json.loads(listed2.output)
    assert [server["name"] for server in payload["servers"]] == ["gpu9"]
    assert payload["servers"][0]["host"] == "9.9.9.9"


def test_cli_gpu_status_local(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    monkeypatch.setattr(remote.shutil, "which", lambda name: None)
    app = typer.Typer()
    remote.add_cli(app)
    result = CliRunner().invoke(app, ["gpu", "status", "--local", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["available"] is False


def test_check_exposes_ok_for_doctor(executor: SSHExecutor) -> None:
    runner = FakeRunner(lambda argv, timeout: RemoteResult(0, CHECK_SAMPLE))
    info = SSHExecutor(executor.server, runner=runner).check()
    assert info["ok"] is True
    assert info["reachable"] is True


def test_cli_ssh_sync(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home-sync"
    home.mkdir()
    (home / "config.toml").write_text(
        '[[servers]]\nname = "gpu1"\nhost = "1.2.3.4"\nuser = "root"\nworkdir = "~/sciforge"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SCIFORGE_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_upload(self, paths, remote_dir, *, delete=False, excludes=None, timeout=1800):
        captured.update(paths=[str(path) for path in paths], remote_dir=remote_dir, excludes=excludes)
        return RemoteResult(0, "sent 12 bytes")

    monkeypatch.setattr(remote.SSHExecutor, "upload_paths", fake_upload)
    local_dir = tmp_path / "code"
    local_dir.mkdir()
    (local_dir / "train.py").write_text("print(1)")
    app = typer.Typer()
    remote.add_cli(app)
    result = CliRunner().invoke(
        app,
        ["ssh", "sync", "gpu1", str(local_dir), "./sciforge", "--exclude", "data", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert captured["paths"][0] == str(local_dir) + "/"
    assert captured["remote_dir"] == "./sciforge"
    assert captured["excludes"] == ["data"]


def test_gpu_status_explicit_unknown_server_fails(ctx_no_server: ToolContext) -> None:
    result = remote.GpuStatusTool().run(ctx_no_server, server="gpu1")
    assert result.ok is False
    assert "未找到服务器" in result.content


def test_cli_ssh_exec_dangerous(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    app = typer.Typer()
    remote.add_cli(app)
    result = CliRunner().invoke(app, ["ssh", "exec", "gpu1", "rm -rf /", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "危险命令" in payload["error"]


def test_registered_remote_tools() -> None:
    from sciforge.core.registry import get_tool

    for name in (
        "remote_run",
        "remote_sync",
        "remote_tail",
        "gpu_status",
        "remote_jobs",
        "remote_kill",
        "slurm_submit",
        "slurm_status",
        "slurm_logs",
    ):
        tool = get_tool(name)
        assert tool is not None, f"工具未注册: {name}"
        assert "remote" in tool.tags
        assert tool.parameters.get("type") == "object"


# ---------------------------------------------------------------------------
# SSH 命令解析 / 密码认证（AskPass）
# ---------------------------------------------------------------------------


def test_parse_ssh_command_variants() -> None:
    from sciforge.tools.remote import parse_ssh_command

    parsed = parse_ssh_command("ssh -p 48322 root@connect.bjb2.seetacloud.com")
    assert parsed["host"] == "connect.bjb2.seetacloud.com"
    assert parsed["port"] == 48322
    assert parsed["user"] == "root"

    parsed = parse_ssh_command("ssh -i ~/.ssh/id_ed25519 -p 2222 lab@10.0.0.9")
    assert parsed["user"] == "lab"
    assert parsed["port"] == 2222
    assert parsed["identity_file"] == "~/.ssh/id_ed25519"

    parsed = parse_ssh_command("sshpass -p secret ssh root@gpu.example.com")
    assert parsed["host"] == "gpu.example.com"
    assert parsed["user"] == "root"

    parsed = parse_ssh_command("ssh my-gpu")
    assert parsed["host"] == "my-gpu"
    assert parsed["user"] == "root"

    with pytest.raises(ValueError):
        parse_ssh_command("")
    with pytest.raises(ValueError):
        parse_ssh_command("scp local.txt host:/tmp")


def test_password_auth_argv_and_askpass() -> None:
    from pathlib import Path

    from sciforge.config import ServerConfig
    from sciforge.tools.remote import SSHExecutor

    server = ServerConfig(
        name="gpu1", host="connect.example.com", port=48322, user="root", password="pw-secret"
    )
    executor = SSHExecutor(server)
    argv = executor.ssh_argv("echo hi")
    assert "BatchMode=yes" not in argv
    assert "NumberOfPasswordPrompts=1" in argv
    assert argv[argv.index("-p") + 1] == "48322"

    env = executor._askpass_env()
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env["SCIFORGE_SSH_PASSWORD"] == "pw-secret"
    script = Path(env["SSH_ASKPASS"])
    assert script.exists()
    assert "pw-secret" not in script.read_text(encoding="utf-8")
    assert "SCIFORGE_SSH_PASSWORD" in script.read_text(encoding="utf-8")


def test_password_execute_passes_askpass_kwargs() -> None:
    from sciforge.config import ServerConfig
    from sciforge.tools.remote import RemoteResult, SSHExecutor

    server = ServerConfig(name="gpu1", host="h", user="root", password="pw")
    captured: dict = {}

    def runner(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = argv
        captured.update(kwargs)
        return RemoteResult(0, "ok", "")

    executor = SSHExecutor(server, runner=runner)
    result = executor.run_raw("true")
    assert result.rc == 0
    assert captured["start_new_session"] is True
    assert captured["stdin_devnull"] is True
    assert captured["env"]["SSH_ASKPASS_REQUIRE"] == "force"

    # 无密码时不应注入 askpass 参数（兼容旧 runner）
    plain = SSHExecutor(ServerConfig(name="k", host="h", user="root"), runner=runner)
    captured.clear()
    plain.run_raw("true")
    assert "env" not in captured
