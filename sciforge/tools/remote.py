"""远程执行层：SSH / rsync / tmux / Slurm + GPU 状态。

设计要点（便于单测，不依赖真实服务器）：

- **argv 构造** 与 **进程执行** 分离：所有要跑的本地进程都经过
  :func:`_run_process`，测试 monkeypatch 它即可，不会真的连 SSH。
- ``ssh``/``rsync``/``scp`` 一律走系统命令（``subprocess``），不引入新依赖。
- 纯函数解析器：:func:`parse_nvidia_smi` / :func:`parse_squeue` / :func:`parse_sacct`
  / :func:`parse_check_output` 等，输入远端文本、输出结构化 dict。
- 工具通过 ``register_tool(...)`` 自注册；CLI 通过 :func:`add_cli` 挂
  ``sciforge ssh ...`` / ``sciforge gpu ...`` 子命令（不创建根 app）。

安全边界：

- 远端命令默认 ``set -euo pipefail`` + ``cd <workdir>``，并做危险命令静态拒绝
  （``rm -rf /``、``mkfs``、``shutdown`` 等）。
- 远端路径默认限制在 ``server.workdir`` 下；用户显式传绝对路径时允许，
  但结果里会给出提示。
"""

from __future__ import annotations

import json
import os
import platform
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sciforge.config import ServerConfig, get_settings
from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SSH_CONNECT_TIMEOUT = 10
SSH_OPTIONS = (
    "-o",
    "BatchMode=yes",
    "-o",
    f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ServerAliveInterval=30",
)

NVIDIA_SMI_QUERY = (
    "nvidia-smi --query-gpu=index,name,memory.total,memory.used,"
    "utilization.gpu,temperature.gpu --format=csv,noheader,nounits"
)
NVIDIA_SMI_APPS_QUERY = (
    "nvidia-smi --query-compute-apps=pid,process_name,used_memory "
    "--format=csv,noheader,nounits"
)

_DEFAULT_EXCLUDES = ("runs/", "results/", ".git/", "__pycache__/", ".venv/", "*.pyc", ".DS_Store")

# `set -euo pipefail` 之外的破坏性命令（宁可误杀，不可放任）
_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"rm\s+(-[a-zA-Z]*\s+)*(-[a-zA-Z]*[rf][a-zA-Z]*|\s)*\s*(/|/\*|~|\$HOME)(\s|$)", "删除根目录/家目录"),
    (r"\brm\s+--no-preserve-root\b", "删除根目录"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\b(wipefs|shred)\b", "擦除磁盘"),
    (r"\bdd\b[^\n;|&]*\bof=/dev/(sd|nvme|hd|disk)", "直接写块设备"),
    (r">\s*/dev/(sd|nvme|hd|disk)\w*", "直接写块设备"),
    (r"\bshutdown\b", "关机"),
    (r"\breboot\b", "重启"),
    (r"\bpoweroff\b", "关机"),
    (r"\bhalt\b", "停机"),
    (r"\binit\s+[06]\b", "切换运行级别"),
    (r"chmod\s+-R\s+(777|000)\s+/(\s|$)", "递归修改根目录权限"),
    (r"chown\s+-R\s+\S+\s+/(\s|$)", "递归修改根目录属主"),
    (r":\(\)\s*\{\s*:\s*\|\s*:", "fork bomb"),
    (r"\bkill\s+-9\s+(-1|1)\b", "杀死全部进程"),
    (r"\b(mv|cp)\s+[^\n;|&]*\s+/(\s|$)", "覆盖根目录"),
)


# ---------------------------------------------------------------------------
# 基础结果类型
# ---------------------------------------------------------------------------


@dataclass
class RemoteResult:
    """一次远端/本地命令执行结果。

    支持 ``rc, stdout, stderr = result`` 解包，便于调用方直接使用。
    """

    rc: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_s: float = 0.0
    argv: list[str] | None = None

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def __iter__(self):
        yield self.rc
        yield self.stdout
        yield self.stderr

    def to_dict(self) -> dict[str, Any]:
        return {
            "rc": self.rc,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "ok": self.ok,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 3),
            "argv": self.argv,
        }


def parse_ssh_command(text: str) -> dict[str, Any]:
    """解析用户粘贴的 ``ssh`` 登录命令，提取 host / port / user / key。

    支持：``ssh -p 48322 root@host``、``ssh root@host -p 2222``、
    ``ssh -i ~/.ssh/id_ed25519 -p 22 user@host``、``sshpass -p xxx ssh root@host``、
    以及裸 ``user@host:port``。无法识别 host 时抛 :class:`ValueError`。
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("请粘贴 ssh 登录命令，例如：ssh -p 48322 root@connect.example.com")
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(f"命令解析失败：{exc}") from exc
    # 兼容 sshpass -p xxx ssh ... / 前置代理命令：从 ssh 开始解析
    for index, token in enumerate(tokens):
        base = os.path.basename(token)
        if base in ("ssh", "ssh.exe"):
            tokens = tokens[index + 1 :]
            break
    else:
        # 没有显式 ssh 命令时，只接受单个裸目标（user@host / host / host:port）
        if len(tokens) != 1:
            raise ValueError("没有找到 ssh 命令；请粘贴完整的 ssh 登录命令，例如：ssh -p 48322 root@host")
        bare = os.path.basename(tokens[0]).lower()
        if bare in ("scp", "rsync", "sshpass", "ssh-copy-id", "sftp"):
            raise ValueError("这不是 ssh 登录命令；请粘贴 ssh 命令或只填 user@host")
        if not ("@" in tokens[0] or "." in tokens[0] or tokens[0].count(":") == 1 or tokens[0].replace("-", "").isalnum()):
            raise ValueError("没有解析出 host；示例：ssh -p 48322 root@connect.example.com")
    parsed: dict[str, Any] = {
        "host": "",
        "port": 22,
        "user": "",
        "identity_file": None,
        "proxy_jump": None,
        "raw": raw,
    }
    target: str | None = None
    index = 0
    # 需要吞掉参数的 ssh 选项（避免把参数误当 host）
    takes_value = {"-p", "--port", "-i", "--identity-file", "-J", "--proxy-jump", "-l", "-o", "-F", "-c", "-m", "-L", "-R", "-D", "-W", "-b", "-e", "-E", "-I", "-S"}
    while index < len(tokens):
        token = tokens[index]
        if token in ("-p", "--port") and index + 1 < len(tokens):
            try:
                parsed["port"] = int(tokens[index + 1])
            except ValueError:
                pass
            index += 2
            continue
        if token in ("-i", "--identity-file") and index + 1 < len(tokens):
            parsed["identity_file"] = tokens[index + 1]
            index += 2
            continue
        if token in ("-J", "--proxy-jump") and index + 1 < len(tokens):
            parsed["proxy_jump"] = tokens[index + 1]
            index += 2
            continue
        if token == "-l" and index + 1 < len(tokens):
            parsed["user"] = tokens[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            index += 2 if token in takes_value else 1
            continue
        if target is None:
            target = token
        index += 1
    if target:
        if "@" in target:
            parsed["user"], target = target.rsplit("@", 1)
        if target.startswith("[") and "]" in target:  # [ipv6]:port
            host, _, maybe_port = target[1:].partition("]")
            parsed["host"] = host
            if maybe_port.startswith(":") and maybe_port[1:].isdigit():
                parsed["port"] = int(maybe_port[1:])
        elif target.count(":") == 1 and target.rsplit(":", 1)[1].isdigit():
            host, port_text = target.rsplit(":", 1)
            parsed["host"] = host
            parsed["port"] = int(port_text)
        else:
            parsed["host"] = target
    if not parsed["host"]:
        raise ValueError("没有解析出 host；示例：ssh -p 48322 root@connect.bjb2.seetacloud.com")
    if not parsed["user"]:
        parsed["user"] = "root"
    return parsed


def _run_process(
    argv: Sequence[str],
    *,
    timeout: float = 60,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    start_new_session: bool = False,
    stdin_devnull: bool = False,
) -> RemoteResult:
    """执行本地进程（唯一出口；测试 monkeypatch 这里即可）。

    不抛异常：找不到可执行文件/超时都转成 ``RemoteResult``。
    """
    started = time.time()
    argv_list = [str(item) for item in argv]
    proc_env = os.environ.copy()
    if env:
        proc_env.update({str(k): str(v) for k, v in env.items()})
    try:
        proc = subprocess.run(  # noqa: S603 - 参数列表形式，无 shell 注入
            argv_list,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=proc_env,
            input=input_text,
            start_new_session=start_new_session,
            stdin=subprocess.DEVNULL if stdin_devnull else None,
        )
        return RemoteResult(
            rc=int(proc.returncode),
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_s=time.time() - started,
            argv=argv_list,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return RemoteResult(
            rc=124,
            stdout=stdout,
            stderr=(stderr + f"\n命令超时（>{timeout}s）").strip(),
            timed_out=True,
            duration_s=time.time() - started,
            argv=argv_list,
        )
    except FileNotFoundError as exc:
        return RemoteResult(
            rc=127,
            stderr=f"找不到可执行文件：{argv_list[0]}（{exc}）",
            duration_s=time.time() - started,
            argv=argv_list,
        )
    except OSError as exc:  # pragma: no cover - 少见的系统错误
        return RemoteResult(
            rc=126,
            stderr=f"执行失败：{exc}",
            duration_s=time.time() - started,
            argv=argv_list,
        )


# ---------------------------------------------------------------------------
# shell / 安全 / 路径 工具函数
# ---------------------------------------------------------------------------


def quote(value: str) -> str:
    """POSIX shell 单引号转义。"""
    return shlex.quote(str(value))


_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_@%+=:,./~-]+$")


def quote_path(path: str) -> str:
    """远端路径转义：安全字符原样输出；含空格等特殊字符时保留 ``~`` 展开能力。"""
    raw = str(path)
    if raw and _SAFE_PATH_RE.match(raw):
        return raw
    if raw.startswith("~"):
        head, sep, rest = raw.partition("/")
        if sep and rest:
            return head + "/" + shlex.quote(rest)
        return head or "~"
    return shlex.quote(raw)


def ssh_available() -> bool:
    return shutil.which("ssh") is not None


def rsync_available() -> bool:
    return shutil.which("rsync") is not None


def scp_available() -> bool:
    return shutil.which("scp") is not None


def is_dangerous_command(command: str) -> str | None:
    """检查命令是否包含明显破坏性操作；返回原因，安全则返回 None。"""
    text = " ".join(str(command or "").split())
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, text):
            return reason
    return None


def ensure_safe_command(command: str) -> None:
    reason = is_dangerous_command(command)
    if reason:
        raise PermissionError(f"拒绝执行危险命令（{reason}）：{command[:200]}")


def conda_activate_script(conda_env: str) -> str:
    """生成可在 ``bash -lc`` 中执行的 conda 激活片段。"""
    env = quote(conda_env)
    return (
        'if command -v conda >/dev/null 2>&1; then eval "$(conda shell.bash hook)"; fi\n'
        'if ! command -v conda >/dev/null 2>&1 && [ -n "${CONDA_EXE:-}" ]; then '
        'eval "$("$CONDA_EXE" shell.bash hook)"; fi\n'
        f"if command -v conda >/dev/null 2>&1; then conda activate {env}; else "
        f'echo "SciForge: 未找到 conda，跳过 conda activate {conda_env}" >&2; fi'
    )


def build_conda_run(command: str, conda_env: str, *, strict: bool = True) -> str:
    """用 ``conda run`` 包裹命令（短命令场景；长任务请用 activate 方式）。"""
    inner = command if strict else command
    prefix = "set -euo pipefail; " if strict else ""
    return f"bash -lc {quote(prefix + f'conda run --no-capture-output -n {quote(conda_env)} ' + inner)}"


def wrap_remote_command(
    command: str,
    *,
    workdir: str | None = None,
    conda_env: str | None = None,
    env: Mapping[str, str] | None = None,
    strict: bool = True,
) -> str:
    """把用户命令包装成远端可执行的 ``bash -lc '...'`` 字符串。"""
    lines: list[str] = []
    if strict:
        lines.append("set -euo pipefail")
    if workdir:
        lines.append(f"cd {quote_path(workdir)}")
    for key, value in (env or {}).items():
        lines.append(f"export {key}={quote(str(value))}")
    if conda_env:
        lines.append(conda_activate_script(conda_env))
    lines.append(command)
    script = "\n".join(line for line in lines if line.strip())
    return "bash -lc " + quote(script)


def resolve_remote_path(
    path: str,
    workdir: str | None,
    *,
    allow_outside: bool = True,
) -> tuple[str, str | None]:
    """把用户给的路径解析为远端路径；返回 ``(path, warning)``。

    相对路径拼到 ``workdir``；绝对路径原样使用但若不在 workdir 下给出提示。
    """
    raw = str(path or ".")
    workdir = workdir or "~"
    warning: str | None = None
    if raw.startswith("/"):
        resolved = posixpath.normpath(raw)
        base = posixpath.normpath(workdir).rstrip("/")
        if base and base not in ("/", "~") and not (resolved == base or resolved.startswith(base + "/")):
            warning = f"远端路径 {raw} 不在工作目录 {workdir} 下，请确认这是有意为之。"
            if not allow_outside:
                raise PermissionError(warning)
    elif raw.startswith("~"):
        resolved = posixpath.normpath(raw)
    else:
        resolved = posixpath.normpath(posixpath.join(workdir, raw))
        base = posixpath.normpath(workdir)
        if base not in ("/", "~") and not (resolved == base or resolved.startswith(base + "/")):
            warning = f"相对路径 {raw} 越出工作目录 {workdir}，请确认这是有意为之。"
            if not allow_outside:
                raise PermissionError(warning)
    return resolved, warning


def _sync_source_path(path: str | Path) -> str:
    """本地源路径：保留末尾 "/"（rsync 用它表示「同步目录内容」）。"""
    raw = str(path)
    expanded = str(Path(raw).expanduser())
    if raw.endswith("/") and not expanded.endswith("/"):
        expanded += "/"
    return expanded


# ---------------------------------------------------------------------------
# SSHExecutor
# ---------------------------------------------------------------------------


class SSHExecutor:
    """一台 GPU 服务器上的命令执行器（纯 argv 构造 + 单点执行）。"""

    def __init__(
        self,
        server: ServerConfig,
        *,
        runner: Any | None = None,
        timeout: float = 60,
    ) -> None:
        self.server = server
        self.workdir = str(server.workdir or "~/sciforge")
        self.conda_env = server.conda_env
        self.env = dict(server.env or {})
        self._runner = runner
        self.default_timeout = timeout

    # -- 基础信息 ---------------------------------------------------------
    @property
    def target(self) -> str:
        user = str(self.server.user or "").strip()
        return f"{user}@{self.server.host}" if user else str(self.server.host)

    @property
    def session(self) -> str:
        return str(self.server.tmux_session or "sciforge")

    # -- 认证（密码 / Key / ssh-config） -----------------------------------
    @property
    def password(self) -> str:
        """密码来源：password_env 优先，其次本机配置里的 password。"""
        env_name = str(getattr(self.server, "password_env", "") or "").strip()
        if env_name:
            value = os.environ.get(env_name, "")
            if value:
                return value
        return str(getattr(self.server, "password", "") or "")

    @property
    def uses_password(self) -> bool:
        return bool(self.password)

    def _askpass_script(self) -> Path:
        """生成只读密码的 askpass 脚本（不把密码写进脚本/命令行）。"""
        cached = getattr(self, "_askpass_path", None)
        if cached and Path(str(cached)).exists():
            return Path(str(cached))
        import tempfile

        handle, name = tempfile.mkstemp(prefix="sciforge-askpass-", suffix=".sh")
        os.close(handle)
        path = Path(name)
        path.write_text('#!/bin/sh\nprintf \'%s\\n\' "${SCIFORGE_SSH_PASSWORD:-}"\n', encoding="utf-8")
        try:
            path.chmod(0o700)
        except OSError:
            pass
        self._askpass_path = str(path)
        return path

    def _askpass_env(self) -> dict[str, str]:
        return {
            "SSH_ASKPASS": str(self._askpass_script()),
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
            "SCIFORGE_SSH_PASSWORD": self.password,
        }

    def _ssh_options(self) -> list[str]:
        """基础 ssh 选项；密码登录时必须去掉 BatchMode=yes。"""
        options = list(SSH_OPTIONS)
        if not self.uses_password:
            return options
        filtered: list[str] = []
        index = 0
        while index < len(options):
            if options[index] == "-o" and index + 1 < len(options):
                if options[index + 1] == "BatchMode=yes":
                    index += 2
                    continue
                filtered += [options[index], options[index + 1]]
                index += 2
                continue
            filtered.append(options[index])
            index += 1
        filtered += ["-o", "NumberOfPasswordPrompts=1"]
        return filtered

    def _execute(self, argv: Sequence[str], *, timeout: float) -> RemoteResult:
        """单点执行：密码登录时注入 askpass 环境并脱离终端。"""
        runner = self._runner_fn()
        if not self.uses_password:
            return runner(argv, timeout=timeout)
        kwargs = {
            "timeout": timeout,
            "env": self._askpass_env(),
            "start_new_session": True,
            "stdin_devnull": True,
        }
        try:
            return runner(argv, **kwargs)
        except TypeError:  # 测试/自定义 runner 只接受 (argv, timeout=...)
            return runner(argv, timeout=timeout)

    def _runner_fn(self):
        return self._runner or _run_process

    # -- argv 构造 --------------------------------------------------------
    def ssh_argv(self, remote_command: str) -> list[str]:
        """构造完整 ssh argv；测试直接断言该列表。"""
        argv: list[str] = ["ssh", *self._ssh_options()]
        port = int(self.server.port or 22)
        if port != 22:
            argv += ["-p", str(port)]
        if self.server.identity_file:
            argv += ["-i", str(Path(self.server.identity_file).expanduser())]
        if self.server.proxy_jump:
            argv += ["-J", str(self.server.proxy_jump)]
        argv.append(self.target)
        argv.append(remote_command)
        return argv

    def rsync_ssh_string(self) -> str:
        """rsync 的 ``-e`` 参数（内嵌 ssh 选项）。"""
        parts: list[str] = ["ssh", *self._ssh_options()]
        port = int(self.server.port or 22)
        if port != 22:
            parts += ["-p", str(port)]
        if self.server.identity_file:
            parts += ["-i", str(Path(self.server.identity_file).expanduser())]
        if self.server.proxy_jump:
            parts += ["-J", str(self.server.proxy_jump)]
        return " ".join(parts)

    def rsync_target(self, remote_path: str) -> str:
        return f"{self.target}:{remote_path}"

    def build_upload_argv(
        self,
        local_paths: Sequence[str | Path],
        remote_dir: str,
        *,
        delete: bool = False,
        excludes: Iterable[str] | None = None,
    ) -> list[str]:
        exclude_list = list(excludes) if excludes is not None else list(self.server.sync_excludes or ())
        remote, _ = resolve_remote_path(remote_dir, self.workdir, allow_outside=True)
        if remote_dir.endswith("/") and not remote.endswith("/"):
            remote += "/"
        if rsync_available():
            argv: list[str] = ["rsync", "-az", "--partial", "--human-readable"]
            if delete:
                argv.append("--delete")
            for pattern in exclude_list:
                argv.append(f"--exclude={pattern}")
            argv += ["-e", self.rsync_ssh_string()]
            argv += [_sync_source_path(p) for p in local_paths]
            argv.append(self.rsync_target(remote))
            return argv
        # scp 兜底
        argv = ["scp", "-r", *self._ssh_options()]
        port = int(self.server.port or 22)
        if port != 22:
            argv += ["-P", str(port)]
        if self.server.identity_file:
            argv += ["-i", str(Path(self.server.identity_file).expanduser())]
        if self.server.proxy_jump:
            argv += ["-J", str(self.server.proxy_jump)]
        argv += [_sync_source_path(p) for p in local_paths]
        argv.append(self.rsync_target(remote))
        return argv

    def build_download_argv(
        self,
        remote_paths: Sequence[str],
        local_dir: str | Path,
        *,
        delete: bool = False,
        excludes: Iterable[str] | None = None,
    ) -> list[str]:
        local = str(Path(local_dir).expanduser())
        resolved = [resolve_remote_path(p, self.workdir, allow_outside=True)[0] for p in remote_paths]
        if rsync_available():
            argv = ["rsync", "-az", "--partial", "--human-readable"]
            if delete:
                argv.append("--delete")
            for pattern in list(excludes or ()):
                argv.append(f"--exclude={pattern}")
            argv += ["-e", self.rsync_ssh_string()]
            argv += [self.rsync_target(p) for p in resolved]
            argv.append(local + "/")
            return argv
        argv = ["scp", "-r", *self._ssh_options()]
        port = int(self.server.port or 22)
        if port != 22:
            argv += ["-P", str(port)]
        if self.server.identity_file:
            argv += ["-i", str(Path(self.server.identity_file).expanduser())]
        if self.server.proxy_jump:
            argv += ["-J", str(self.server.proxy_jump)]
        argv += [self.rsync_target(p) for p in resolved]
        argv.append(local)
        return argv

    # -- 执行 -------------------------------------------------------------
    def run_raw(self, remote_command: str, *, timeout: float | None = None) -> RemoteResult:
        """执行一条已经拼好的远端 shell 命令（通常是 raw 字符串）。"""
        argv = self.ssh_argv(remote_command)
        return self._execute(argv, timeout=float(timeout or self.default_timeout))

    def run(
        self,
        command: str,
        *,
        timeout: float = 60,
        wrap: bool = True,
        workdir: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> RemoteResult:
        """在服务器上执行短命令，默认包装 ``set -euo pipefail; cd workdir``。"""
        if wrap:
            merged_env = {**self.env, **dict(extra_env or {})}
            wrapped = wrap_remote_command(
                command,
                workdir=workdir or self.workdir,
                conda_env=self.conda_env,
                env=merged_env,
            )
        else:
            wrapped = command
        return self.run_raw(wrapped, timeout=timeout)

    def check(self, *, timeout: float = 30) -> dict[str, Any]:
        """连通性 + 远端环境探测；永不抛异常。"""
        result = self.run_raw(CHECK_SCRIPT, timeout=timeout)
        info = parse_check_output(result.stdout, rc=result.rc)
        info["server"] = self.server.name
        info["host"] = self.server.host
        info["reachable"] = result.rc == 0
        info["ok"] = info["reachable"]  # doctor 等调用方统一读 ok 字段
        if result.rc != 0:
            info["error"] = (result.stderr or result.stdout or "ssh 连接失败").strip()[:800]
        if info["reachable"] and bool(self.server.auto_mkdir):
            mkdir = self.run_raw(f"mkdir -p {quote(self.workdir)}", timeout=timeout)
            info["workdir_ready"] = mkdir.rc == 0
            if mkdir.rc != 0:
                info["workdir_error"] = mkdir.stderr.strip()[:400]
        return info

    def ensure_workdir(self, *, timeout: float = 30) -> RemoteResult:
        return self.run_raw(
            f"mkdir -p {quote_path(self.workdir)} && cd {quote_path(self.workdir)} && pwd",
            timeout=timeout,
        )

    def upload_paths(
        self,
        local_paths: Sequence[str | Path],
        remote_dir: str,
        *,
        delete: bool = False,
        excludes: Iterable[str] | None = None,
        timeout: float = 1800,
    ) -> RemoteResult:
        argv = self.build_upload_argv(local_paths, remote_dir, delete=delete, excludes=excludes)
        return self._execute(argv, timeout=timeout)

    def download_paths(
        self,
        remote_paths: Sequence[str],
        local_dir: str | Path,
        *,
        delete: bool = False,
        excludes: Iterable[str] | None = None,
        timeout: float = 1800,
    ) -> RemoteResult:
        Path(local_dir).expanduser().mkdir(parents=True, exist_ok=True)
        argv = self.build_download_argv(remote_paths, local_dir, delete=delete, excludes=excludes)
        return self._execute(argv, timeout=timeout)

    # -- 后台任务 ---------------------------------------------------------
    def build_detached_script(
        self,
        command: str,
        *,
        log_path: str,
        workdir: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> str:
        """后台任务脚本：stdout/stderr 全部重定向到日志文件。"""
        workdir = workdir or self.workdir
        log_dir = posixpath.dirname(log_path)
        lines = ["set -euo pipefail"]
        if log_dir:
            lines.append(f"mkdir -p {quote_path(log_dir)}")
        lines.append(f"exec >> {quote_path(log_path)} 2>&1")
        lines.append(f"cd {quote_path(workdir)}")
        for key, value in {**self.env, **dict(extra_env or {})}.items():
            lines.append(f"export {key}={quote(str(value))}")
        if self.conda_env:
            lines.append(conda_activate_script(self.conda_env))
        lines.append(command)
        return "\n".join(lines)

    def run_detached(
        self,
        name: str,
        command: str,
        log_path: str | None = None,
        *,
        session: str | None = None,
        timeout: float = 60,
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """tmux 后台启动；tmux 不可用时退回 nohup + PID 文件。

        返回 job 描述 dict（mode/session/window/log_path/job_id/pid/command）。
        """
        session = session or self.session
        log_path = log_path or posixpath.join(self.workdir, "runs", name, "train.log")
        log_path, log_warning = resolve_remote_path(log_path, self.workdir)
        script = self.build_detached_script(command, log_path=log_path, extra_env=extra_env)
        job: dict[str, Any] = {
            "mode": "tmux",
            "name": name,
            "session": session,
            "window": name,
            "log_path": log_path,
            "command": command,
            "job_id": f"{session}:{name}",
        }

        has_session = self.run_raw(f"tmux has-session -t {quote(session)}", timeout=timeout)
        if has_session.rc == 0:
            argv = ["tmux", "new-window", "-d", "-t", session, "-n", name, "bash", "-lc", script]
        else:
            argv = ["tmux", "new-session", "-d", "-s", session, "-n", name, "bash", "-lc", script]
        tmux_cmd = " ".join(quote(part) for part in argv)
        tmux_result = self.run_raw(tmux_cmd, timeout=timeout)
        if tmux_result.rc == 0:
            job["mode"] = "tmux"
            job["tmux_error"] = None
            if log_warning:
                job["warning"] = log_warning
            return job

        job["mode"] = "nohup"
        job["tmux_error"] = (tmux_result.stderr or tmux_result.stdout or "").strip()[:400]
        job["warning"] = log_warning
        log_dir = posixpath.dirname(log_path)
        pid_file = posixpath.join(log_dir or ".", f"{name}.pid")
        mkdir = f"mkdir -p {quote_path(log_dir)} && " if log_dir else ""
        nohup = (
            f"{mkdir}nohup bash -lc {quote(script)} > /dev/null 2>&1 < /dev/null & "
            f"echo $! > {quote_path(pid_file)}; cat {quote_path(pid_file)}"
        )
        result = self.run_raw(nohup, timeout=timeout)
        if result.rc != 0:
            raise RuntimeError(
                f"后台启动失败（tmux+{job['mode']}）：{(result.stderr or result.stdout).strip()[:500]}"
            )
        pid_match = re.search(r"\d+", result.stdout or "")
        pid = int(pid_match.group()) if pid_match else None
        job["pid"] = pid
        job["pid_file"] = pid_file
        job["job_id"] = str(pid) if pid else f"{session}:{name}"
        return job

    # -- 查询 / 停止 ------------------------------------------------------
    def tail_logs(self, log_path: str, *, lines: int = 100, timeout: float = 30) -> RemoteResult:
        resolved, _ = resolve_remote_path(log_path, self.workdir)
        lines = max(1, min(int(lines), 2000))
        return self.run_raw(f"tail -n {lines} {quote_path(resolved)}", timeout=timeout)

    def list_tmux_sessions(self, *, timeout: float = 20) -> list[dict[str, Any]]:
        result = self.run_raw("tmux ls 2>/dev/null || true", timeout=timeout)
        return parse_tmux_sessions(result.stdout)

    def list_windows(self, session: str | None = None, *, timeout: float = 20) -> list[str]:
        session = session or self.session
        result = self.run_raw(
            f"tmux list-windows -t {quote(session)} -F '#{{window_name}}' 2>/dev/null || true",
            timeout=timeout,
        )
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def list_slurm_jobs(self, *, user: str | None = None, timeout: float = 20) -> list[dict[str, Any]]:
        who = user or str(self.server.user or "").strip()
        cmd = "squeue --noheader --format='%i %P %j %u %T %M %D %R'"
        if who:
            cmd = f"squeue -u {quote(who)} --noheader --format='%i %P %j %u %T %M %D %R'"
        result = self.run_raw(cmd, timeout=timeout)
        return parse_squeue(result.stdout)

    def job_state(self, job_id: str, *, timeout: float = 20) -> dict[str, Any]:
        """查询一个 job 是否存活；job_id 形如 ``session:window``、PID 或 Slurm job id。"""
        raw = str(job_id or "").strip()
        if not raw:
            return {"job_id": raw, "alive": None, "state": "unknown"}
        if ":" in raw and not raw.isdigit():
            session = raw.split(":", 1)[0]
            window = raw.split(":", 1)[1] if ":" in raw else ""
            windows = self.list_windows(session, timeout=timeout)
            alive = bool(windows) and (window in windows or not window)
            return {
                "job_id": raw,
                "alive": alive,
                "state": "running" if alive else "stopped",
                "windows": windows,
            }
        if raw.isdigit():
            if str(self.server.scheduler) == "slurm":
                result = self.run_raw(f"squeue -h -j {quote(raw)} -o '%T'", timeout=timeout)
                state = (result.stdout or "").strip().splitlines()
                state_text = state[0].strip() if state else ""
                return {
                    "job_id": raw,
                    "alive": bool(state_text) and state_text not in ("COMPLETED", "FAILED", "CANCELLED"),
                    "state": state_text.lower() or "unknown",
                }
            result = self.run_raw(f"kill -0 {quote(raw)} 2>/dev/null", timeout=timeout)
            return {
                "job_id": raw,
                "alive": result.rc == 0,
                "state": "running" if result.rc == 0 else "stopped",
            }
        return {"job_id": raw, "alive": None, "state": "unknown"}

    def kill_job(self, job_id: str, *, kind: str = "auto", timeout: float = 30) -> RemoteResult:
        raw = str(job_id or "").strip()
        kind = (kind or "auto").lower()
        if kind == "auto":
            if str(self.server.scheduler) == "slurm" and raw.isdigit():
                kind = "slurm"
            elif ":" in raw or not raw.isdigit():
                kind = "tmux"
            else:
                kind = "pid"
        if kind == "slurm":
            return self.run_raw(f"scancel {quote(raw)}", timeout=timeout)
        if kind == "tmux":
            if ":" in raw:
                return self.run_raw(f"tmux kill-window -t {quote(raw)}", timeout=timeout)
            return self.run_raw(f"tmux kill-session -t {quote(raw)}", timeout=timeout)
        return self.run_raw(f"kill {quote(raw)}", timeout=timeout)

    # -- Slurm ------------------------------------------------------------
    def submit_slurm(
        self,
        script_text: str,
        *,
        run_id: str,
        log_path: str | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """上传 sbatch 脚本并提交，返回 job id 等信息。"""
        runs_dir = posixpath.join(self.workdir, "runs", run_id)
        script_path = posixpath.join(runs_dir, "job.sbatch")
        local_tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"sciforge-{run_id}.sbatch"
        local_tmp.write_text(script_text, encoding="utf-8")
        try:
            upload = self.upload_paths([local_tmp], runs_dir, excludes=[])
        finally:
            try:
                local_tmp.unlink()
            except OSError:
                pass
        if not upload.ok:
            raise RuntimeError(f"上传 sbatch 脚本失败：{upload.stderr.strip()[:500]}")
        result = self.run_raw(f"sbatch {quote_path(script_path)}", timeout=timeout)
        job_id = parse_sbatch_output(result.stdout)
        if result.rc != 0 or job_id is None:
            raise RuntimeError(f"sbatch 提交失败：{(result.stderr or result.stdout).strip()[:500]}")
        return {
            "job_id": str(job_id),
            "script_path": script_path,
            "log_path": log_path or posixpath.join(runs_dir, "slurm-%j.log"),
            "command": script_text,
            "mode": "slurm",
        }

    def slurm_queue(self, *, timeout: float = 20) -> list[dict[str, Any]]:
        return self.list_slurm_jobs(timeout=timeout)

    def slurm_history(self, *, jobs: Sequence[str] | None = None, timeout: float = 30) -> list[dict[str, Any]]:
        jobs_arg = "-j " + ",".join(str(j) for j in jobs) if jobs else ""
        result = self.run_raw(
            "sacct -n -P --format=JobID,JobName,Partition,State,ExitCode,Elapsed,Start,End "
            f"{jobs_arg} 2>/dev/null | head -n 200",
            timeout=timeout,
        )
        return parse_sacct(result.stdout)


# ---------------------------------------------------------------------------
# 远端环境探测脚本 & 解析器
# ---------------------------------------------------------------------------

CHECK_SCRIPT = """\
echo "SCIFORGE_OS=$(uname -srm 2>/dev/null || echo n/a)"
echo "SCIFORGE_HOST=$(hostname 2>/dev/null || echo n/a)"
echo "SCIFORGE_USER=$(whoami 2>/dev/null || echo n/a)"
echo "SCIFORGE_PY=$(python --version 2>&1 | head -1 || echo n/a)"
echo "SCIFORGE_PY_PATH=$(command -v python 2>/dev/null || echo n/a)"
echo "SCIFORGE_TORCH=$(python -c 'import torch;print(torch.__version__)' 2>/dev/null || echo n/a)"
echo "SCIFORGE_CUDA=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo n/a)"
echo "SCIFORGE_GPU_COUNT=$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l | tr -d ' ' || echo 0)"
echo "SCIFORGE_GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -5 | tr '\\n' ';' || echo n/a)"
echo "SCIFORGE_TMUX=$(tmux -V 2>/dev/null || echo n/a)"
echo "SCIFORGE_CONDA=$(conda --version 2>/dev/null || echo n/a)"
echo "SCIFORGE_WORKDIR=$(pwd 2>/dev/null || echo n/a)"
"""


def parse_check_output(text: str, *, rc: int = 0) -> dict[str, Any]:
    """解析 :data:`CHECK_SCRIPT` 的 marker 输出。"""
    info: dict[str, Any] = {
        "os": "",
        "hostname": "",
        "user": "",
        "python": "",
        "python_path": "",
        "torch": "",
        "cuda": "",
        "gpu_count": 0,
        "gpu_names": [],
        "tmux": "",
        "conda": "",
        "workdir": "",
    }
    markers = {
        "SCIFORGE_OS": "os",
        "SCIFORGE_HOST": "hostname",
        "SCIFORGE_USER": "user",
        "SCIFORGE_PY": "python",
        "SCIFORGE_PY_PATH": "python_path",
        "SCIFORGE_TORCH": "torch",
        "SCIFORGE_CUDA": "cuda",
        "SCIFORGE_TMUX": "tmux",
        "SCIFORGE_CONDA": "conda",
        "SCIFORGE_WORKDIR": "workdir",
    }
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("SCIFORGE_"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "SCIFORGE_GPU":
            info["gpu_names"] = [part for part in value.split(";") if part and part != "n/a"]
        elif key == "SCIFORGE_GPU_COUNT":
            try:
                info["gpu_count"] = int(value)
            except ValueError:
                info["gpu_count"] = 0
        elif key in markers:
            info[markers[key]] = "" if value in ("n/a", "None") else value
    if info["gpu_count"] == 0 and info["gpu_names"]:
        info["gpu_count"] = len(info["gpu_names"])
    info["rc"] = rc
    return info


def _to_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip().rstrip("%")))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def parse_nvidia_smi(text: str) -> list[dict[str, Any]]:
    """解析 ``--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu``。

    返回字段：index/name/memory_total_mb/memory_used_mb/memory_free_mb/
    utilization_percent/temperature_c。
    """
    gpus: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("index"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        total = _to_int(parts[2])
        used = _to_int(parts[3])
        entry: dict[str, Any] = {
            "index": _to_int(parts[0]) if parts[0].isdigit() else len(gpus),
            "name": parts[1],
            "memory_total_mb": total,
            "memory_used_mb": used,
            "memory_free_mb": (total - used) if total is not None and used is not None else None,
            "utilization_percent": _to_int(parts[4]) if len(parts) > 4 else None,
            "temperature_c": _to_int(parts[5]) if len(parts) > 5 else None,
        }
        if entry["memory_total_mb"] is not None:
            entry["memory_used_percent"] = (
                round(100.0 * (entry["memory_used_mb"] or 0) / entry["memory_total_mb"], 1)
                if entry["memory_total_mb"]
                else 0.0
            )
        gpus.append(entry)
    return gpus


def parse_nvidia_smi_apps(text: str) -> list[dict[str, Any]]:
    """解析 ``--query-compute-apps=pid,process_name,used_memory``。"""
    apps: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("pid"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        apps.append(
            {
                "pid": _to_int(parts[0]),
                "process_name": parts[1],
                "used_memory_mb": _to_int(parts[2]) if len(parts) > 2 else None,
            }
        )
    return apps


def parse_squeue(text: str) -> list[dict[str, Any]]:
    """解析 ``squeue`` 输出（默认或 ``--format='%i %P %j %u %T %M %D %R'``）。"""
    jobs: list[dict[str, Any]] = []
    header_seen = False
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if not header_seen and line.strip().split()[0].upper() == "JOBID":
            header_seen = True
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        nodelist = parts[7] if len(parts) > 7 else ""
        jobs.append(
            {
                "job_id": parts[0],
                "partition": parts[1],
                "name": parts[2],
                "user": parts[3],
                "state": parts[4],
                "time": parts[5] if len(parts) > 5 else "",
                "nodes": parts[6] if len(parts) > 6 else "",
                "nodelist": " ".join(parts[7:]) if len(parts) > 7 else nodelist,
            }
        )
    return jobs


def parse_sacct(text: str) -> list[dict[str, Any]]:
    """解析 ``sacct -n -P`` 管道分隔输出（含表头时自动识别）。"""
    jobs: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or set(line) <= {"-"}:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        job_id = parts[0].strip()
        if job_id.upper() in ("JOBID", "JOBIDRAW"):
            continue
        # sacct 的 .batch / .extern 子条目跳过
        if "." in job_id:
            continue
        jobs.append(
            {
                "job_id": job_id,
                "name": parts[1].strip(),
                "partition": parts[2].strip(),
                "state": parts[3].strip(),
                "exit_code": parts[4].strip() if len(parts) > 4 else "",
                "elapsed": parts[5].strip() if len(parts) > 5 else "",
                "start": parts[6].strip() if len(parts) > 6 else "",
                "end": parts[7].strip() if len(parts) > 7 else "",
            }
        )
    return jobs


def parse_tmux_sessions(text: str) -> list[dict[str, Any]]:
    """解析 ``tmux ls`` 输出。"""
    sessions: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([^:]+):\s*(\d+)\s+windows?", line)
        if not match:
            sessions.append({"name": line.split(":")[0].strip(), "windows": None})
            continue
        sessions.append({"name": match.group(1).strip(), "windows": int(match.group(2))})
    return sessions


def parse_sbatch_output(text: str) -> int | None:
    """从 ``sbatch`` 输出里取出 job id。"""
    match = re.search(r"Submitted batch job\s+(\d+)", text or "")
    if match:
        return int(match.group(1))
    match = re.search(r"(?m)^\s*(\d+)\s*$", text or "")
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# GPU 表与本地降级
# ---------------------------------------------------------------------------


def format_gpu_table(gpus: Sequence[Mapping[str, Any]], *, title: str | None = None) -> str:
    """把 GPU dict 列表渲染成 Markdown 表。"""
    if not gpus:
        return (title + "\n\n" if title else "") + "（没有解析到 GPU 信息）"
    lines: list[str] = []
    if title:
        lines += [f"### {title}", ""]
    lines.append("| GPU | 型号 | 显存 (GB, 已用/总) | 利用率 | 温度 |")
    lines.append("| ---: | --- | ---: | ---: | ---: |")
    for gpu in gpus:
        total = gpu.get("memory_total_mb")
        used = gpu.get("memory_used_mb")
        mem = "n/a"
        if total is not None:
            mem = f"{(used or 0) / 1024:.1f} / {total / 1024:.1f}"
        util = gpu.get("utilization_percent")
        temp = gpu.get("temperature_c")
        lines.append(
            "| {idx} | {name} | {mem} | {util} | {temp} |".format(
                idx=gpu.get("index", "?"),
                name=gpu.get("name", "?"),
                mem=mem,
                util=f"{util}%" if util is not None else "n/a",
                temp=f"{temp}°C" if temp is not None else "n/a",
            )
        )
    return "\n".join(lines)


def local_gpu_info(*, runner: Any | None = None) -> dict[str, Any]:
    """本地 GPU 降级探测（无服务器配置时使用，永不抛异常）。"""
    runner = runner or _run_process
    system = platform.system()
    machine = platform.machine()
    info: dict[str, Any] = {
        "server": "local",
        "available": False,
        "platform": system,
        "arch": machine,
        "gpus": [],
        "processes": [],
    }
    if system == "Darwin":
        apple = machine in ("arm64", "aarch64")
        info["reason"] = (
            "macOS 没有 nvidia-smi。Apple Silicon 可用 MPS 后端训练（torch.backends.mps.is_available()），"
            "需要 NVIDIA GPU 时请用 `sciforge ssh add <name> ...` 配置 GPU 服务器。"
            if apple
            else "macOS 没有 nvidia-smi。需要 NVIDIA GPU 时请配置 GPU 服务器：`sciforge ssh add <name> ...`。"
        )
        info["mps"] = apple
        return info
    if not shutil.which("nvidia-smi"):
        info["reason"] = "本机未找到 nvidia-smi。若这是无 GPU 的开发机，请配置 GPU 服务器后使用 `sciforge gpu status <server>`。"
        return info
    result = runner([NVIDIA_SMI_QUERY], timeout=10)
    if result.rc != 0:
        info["reason"] = f"本地 nvidia-smi 执行失败：{(result.stderr or result.stdout).strip()[:300]}"
        return info
    gpus = parse_nvidia_smi(result.stdout)
    info["available"] = bool(gpus)
    info["gpus"] = gpus
    if not gpus:
        info["reason"] = "nvidia-smi 返回为空（可能驱动/容器权限问题）。"
    return info


# ---------------------------------------------------------------------------
# 本地执行器（无服务器时的 exp_launch 降级）
# ---------------------------------------------------------------------------

# 同一进程内启动的本地子进程（避免僵尸进程让 job_state 误判「仍在运行」）
_LOCAL_CHILDREN: dict[int, subprocess.Popen] = {}


def _process_exists(pid: int) -> bool:
    """判断 PID 是否仍是活进程（排除僵尸态；跨平台用 ps 兜底）。"""
    proc = _LOCAL_CHILDREN.get(pid)
    if proc is not None and proc.poll() is not None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - 有权限外进程
        return True
    try:
        result = subprocess.run(  # noqa: S603 - 固定 ps 参数
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        return True
    state = (result.stdout or "").strip()
    if not state:
        return False
    return not state.upper().startswith("Z")



class LocalExecutor:
    """本地执行器；与 :class:`SSHExecutor` 保持相近接口，便于实验模块复用。"""

    def __init__(self, *, workdir: str | Path | None = None, runner: Any | None = None) -> None:
        self.workdir = str(Path(workdir).expanduser()) if workdir else str(Path.cwd())
        self._runner = runner

    # SSHExecutor 兼容属性
    @property
    def target(self) -> str:
        return "local"

    @property
    def session(self) -> str:
        return "sciforge-local"

    def _runner_fn(self):
        return self._runner or _run_process

    def run(
        self,
        command: str,
        *,
        timeout: float = 60,
        wrap: bool = True,
        workdir: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> RemoteResult:
        env = {**os.environ, **dict(extra_env or {})} if extra_env else None
        return self._runner_fn()(
            ["bash", "-lc", command],
            timeout=timeout,
            cwd=workdir or self.workdir,
            env=env,
        )

    def run_raw(self, command: str, *, timeout: float | None = None) -> RemoteResult:
        return self.run(command, timeout=float(timeout or 60))

    def check(self, *, timeout: float = 30) -> dict[str, Any]:
        result = self.run(
            "uname -srm; python --version 2>&1; command -v python",
            timeout=timeout,
        )
        info = {
            "server": "local",
            "host": platform.node(),
            "reachable": result.rc == 0,
            "ok": result.rc == 0,
            "workdir": self.workdir,
            "os": platform.platform(),
            "python": platform.python_version(),
            "output": result.stdout.strip()[:800],
            "rc": result.rc,
        }
        if result.rc != 0:
            info["error"] = result.stderr.strip()[:500]
        return info

    def run_detached(
        self,
        name: str,
        command: str,
        log_path: str | None = None,
        *,
        session: str | None = None,
        timeout: float = 60,
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        workdir = Path(self.workdir)
        log_file = Path(log_path) if log_path else workdir / "runs" / name / "train.log"
        if not log_file.is_absolute():
            log_file = workdir / log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)
        proc_env = {**os.environ, **dict(extra_env or {})} if extra_env else None
        handle = log_file.open("ab")
        try:
            proc = subprocess.Popen(  # noqa: S603 - 参数列表
                ["bash", "-lc", command],
                cwd=str(workdir),
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=proc_env,
            )
        finally:
            handle.close()
        _LOCAL_CHILDREN[proc.pid] = proc
        pid_file = log_file.parent / f"{name}.pid"
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        return {
            "mode": "local-nohup",
            "name": name,
            "session": session or "local",
            "window": name,
            "log_path": str(log_file),
            "pid": proc.pid,
            "pid_file": str(pid_file),
            "job_id": str(proc.pid),
            "command": command,
        }

    def tail_logs(self, log_path: str, *, lines: int = 100, timeout: float = 30) -> RemoteResult:
        path = Path(log_path).expanduser()
        if not path.is_absolute():
            path = Path(self.workdir) / path
        if not path.exists():
            return RemoteResult(rc=1, stderr=f"日志不存在：{path}")
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = text_lines[-max(1, min(int(lines), 2000)) :]
        return RemoteResult(rc=0, stdout="\n".join(selected))

    def job_state(self, job_id: str, *, timeout: float = 20) -> dict[str, Any]:
        raw = str(job_id or "").strip()
        if not raw.isdigit():
            return {"job_id": raw, "alive": None, "state": "unknown"}
        alive = _process_exists(int(raw))
        return {"job_id": raw, "alive": alive, "state": "running" if alive else "stopped"}

    def kill_job(self, job_id: str, *, kind: str = "auto", timeout: float = 30) -> RemoteResult:
        raw = str(job_id or "").strip()
        if not raw.isdigit():
            return RemoteResult(rc=1, stderr=f"无法停止本地 job：{raw}")
        pid = int(raw)
        proc = _LOCAL_CHILDREN.get(pid)
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            else:
                os.kill(pid, 15)
            return RemoteResult(rc=0, stdout=f"已发送 SIGTERM 到 {raw}")
        except (OSError, ProcessLookupError) as exc:
            return RemoteResult(rc=1, stderr=str(exc))

    def upload_paths(self, local_paths: Sequence[str | Path], remote_dir: str) -> RemoteResult:
        return RemoteResult(rc=0, stdout="本地执行器无需上传")

    def download_paths(self, remote_paths: Sequence[str], local_dir: str | Path) -> RemoteResult:
        return RemoteResult(rc=0, stdout="本地执行器无需下载")


def make_executor(server: ServerConfig | None, *, local: bool = False, workdir: str | Path | None = None):
    """按配置选执行器：有 server 用 SSH，否则本地。"""
    if server is not None and not local:
        return SSHExecutor(server)
    return LocalExecutor(workdir=workdir)


# ---------------------------------------------------------------------------
# Slurm 脚本生成
# ---------------------------------------------------------------------------


def render_sbatch_script(
    *,
    job_name: str,
    command: str,
    workdir: str,
    conda_env: str | None = None,
    partition: str | None = None,
    gpu_type: str | None = None,
    gpus: int = 1,
    cpus: int = 4,
    mem: str = "32G",
    time: str = "24:00:00",
    log_path: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """生成 sbatch 脚本文本（纯函数，便于测试）。"""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job_name))[:80] or "job"
    log_path = log_path or f"slurm-{safe_name}-%j.log"
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={safe_name}",
        f"#SBATCH --output={log_path}",
        f"#SBATCH --error={log_path}",
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks=1",
        f"#SBATCH --cpus-per-task={int(cpus)}",
        f"#SBATCH --mem={mem}",
        f"#SBATCH --time={time}",
    ]
    if partition:
        lines.append(f"#SBATCH --partition={partition}")
    if gpus:
        resource = f"gpu:{gpu_type}:{int(gpus)}" if gpu_type else f"gpu:{int(gpus)}"
        lines.append(f"#SBATCH --gres={resource}")
    lines.append("")
    lines.append("set -euo pipefail")
    lines.append(f"cd {quote_path(workdir)}")
    for key, value in (env or {}).items():
        lines.append(f"export {key}={quote(str(value))}")
    if conda_env:
        lines.append(conda_activate_script(conda_env))
    lines.append(command)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------


def _server_or_fail(
    ctx: ToolContext,
    name: str | None,
    *,
    tool: str,
) -> tuple[ServerConfig | None, ToolResult | None]:
    server = ctx.get_server(name)
    if server is None:
        configured = ", ".join(s.name for s in (getattr(ctx.settings, "servers", []) or []))
        hint = (
            f"未找到服务器 {name}（已配置：{configured}）" if name else "尚未配置任何 GPU 服务器"
        )
        return None, ToolResult.failure(
            f"{hint}。请先添加：`sciforge ssh add gpu1 --host <host> --user <user> --key ~/.ssh/id_ed25519`"
            f" 或在 ~/.sciforge/config.toml 中追加 [[servers]]。"
        )
    return server, None


def _executor_for(ctx: ToolContext, server: ServerConfig) -> SSHExecutor:
    factory = ctx.extra.get("ssh_executor_factory") if hasattr(ctx, "extra") else None
    if callable(factory):
        return factory(server)
    return SSHExecutor(server)


class RemoteRunTool(BaseTool):
    name = "remote_run"
    description = (
        "在已配置的 GPU 服务器上执行 shell 命令。短命令直接返回输出；"
        "长任务（训练/eval）请传 detached=true，在 tmux 后台运行并返回 job id 与查看日志的命令。"
        "远端工作目录为 server.workdir，命令默认用 conda 环境包装。"
    )
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的远端命令，如 `nvidia-smi`、`python train.py`"},
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "detached": {"type": "boolean", "default": False, "description": "是否 tmux 后台运行"},
            "name": {"type": "string", "description": "后台任务名（detached 时），默认自动生成"},
            "log_path": {"type": "string", "description": "后台任务日志路径（相对 workdir）"},
            "timeout": {"type": "integer", "default": 120, "description": "前台命令超时秒数"},
        },
        "required": ["command"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        command = str(self.require(kwargs, "command"))
        try:
            ensure_safe_command(command)
        except PermissionError as exc:
            return ToolResult.failure(str(exc))
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        executor = _executor_for(ctx, server)
        timeout = max(1, min(int(kwargs.get("timeout") or 120), 3600))
        if bool(kwargs.get("detached")):
            name = str(kwargs.get("name") or f"job-{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                job = executor.run_detached(
                    name,
                    command,
                    kwargs.get("log_path"),
                    timeout=min(timeout, 120),
                )
            except RuntimeError as exc:
                return ToolResult.failure(str(exc))
            tail_cmd = f"sciforge remote_tail {job['log_path']} --server {server.name}"
            content = (
                f"已在 {server.name} 后台启动 {name}（{job['mode']}）\n"
                f"job_id: {job['job_id']}\n"
                f"log: {job['log_path']}\n"
                f"查看日志: {tail_cmd} 或 `tail -f {job['log_path']}`\n"
                f"停止: `sciforge remote_kill {job['job_id']} --server {server.name}`"
            )
            if job.get("tmux_error"):
                content += f"\n（tmux 不可用，已退回 nohup：{job['tmux_error']}）"
            if job.get("warning"):
                content += f"\n⚠️ {job['warning']}"
            return ToolResult.success(content, data={**job, "server": server.name})
        result = executor.run(command, timeout=timeout)
        output = (result.stdout or "") + (("\n[stderr]\n" + result.stderr) if result.stderr else "")
        output = output.strip() or "(无输出)"
        if len(output) > 20_000:
            output = output[:20_000] + "\n…（输出截断）"
        return ToolResult(
            ok=result.ok,
            content=f"$ {command}\n[{server.name}] exit={result.rc}\n{output}",
            data={**result.to_dict(), "server": server.name},
            error=None if result.ok else f"远端命令退出码 {result.rc}",
        )


class RemoteSyncTool(BaseTool):
    name = "remote_sync"
    description = (
        "本地 ↔ GPU 服务器同步文件/目录（rsync，自动套用 server.sync_excludes，scp 兜底）。"
        "上传用于把实验代码送到远端；下载用于取回 metrics/日志/checkpoint。"
    )
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "direction": {"type": "string", "enum": ["upload", "download"], "default": "upload"},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "upload：本地路径列表；download：远端路径列表",
            },
            "remote_dir": {"type": "string", "description": "upload 的远端目标目录（默认 workdir）；download 时为本地目录（默认项目目录）"},
            "delete": {"type": "boolean", "default": False, "description": "是否 --delete（谨慎）"},
            "excludes": {"type": "array", "items": {"type": "string"}, "description": "额外排除模式"},
        },
        "required": ["paths"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        paths = kwargs.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            return ToolResult.failure("paths 不能为空")
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        executor = _executor_for(ctx, server)
        direction = str(kwargs.get("direction") or "upload").lower()
        delete = bool(kwargs.get("delete"))
        excludes = [str(item) for item in (kwargs.get("excludes") or [])]
        if direction == "upload":
            local_paths = [self.as_path(p, ctx.project_dir) for p in paths]
            missing = [str(p) for p in local_paths if not p.exists()]
            if missing:
                return ToolResult.failure(f"本地路径不存在：{', '.join(missing)}")
            remote_dir = str(kwargs.get("remote_dir") or executor.workdir)
            result = executor.upload_paths(local_paths, remote_dir, delete=delete, excludes=excludes)
            target = remote_dir
        else:
            local_dir = self.as_path(kwargs.get("remote_dir") or ctx.project_dir, ctx.project_dir)
            result = executor.download_paths(
                [str(p) for p in paths], local_dir, delete=delete, excludes=excludes
            )
            target = str(local_dir)
            downloaded = [
                str(local_dir / Path(str(item)).name)
                for item in paths
                if (local_dir / Path(str(item)).name).is_file()
            ]
        if not result.ok:
            return ToolResult.failure(
                f"同步失败（rc={result.rc}）：{(result.stderr or result.stdout).strip()[:800]}",
                content=result.stderr[:2000],
            )
        artifacts = downloaded if direction == "download" else []
        return ToolResult.success(
            f"同步完成（{direction} → {target}）\n{(result.stdout or '').strip()[-2000:]}",
            data={**result.to_dict(), "server": server.name, "direction": direction, "target": target},
            artifacts=artifacts,
        )


METRICS_DISCOVERY = (
    "ls -t "
    "{workdir}/runs/*/metrics.json {workdir}/runs/*/*/metrics.json "
    "{workdir}/metrics.json {workdir}/*/metrics.json 2>/dev/null | head -5"
)


def parse_metrics_payload(raw: str) -> dict[str, Any]:
    """把 metrics.json / JSONL / 历史记录解析成 latest + series，供工具与 GUI 复用。"""
    text = (raw or "").strip()
    if not text:
        return {"latest": {}, "series": {}, "points": 0, "error": "指标文件为空"}
    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        if rows:
            data = rows
    if data is None:
        return {"latest": {}, "series": {}, "points": 0, "error": "不是可解析的 JSON / JSONL 指标"}

    # 常见包装：{"history": [...]} / {"epochs": [...]} / {"metrics": [...]}
    if isinstance(data, dict):
        for key in ("history", "epochs", "records", "steps", "metrics", "log"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    def numeric(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    if isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
        if not rows:
            return {"latest": {}, "series": {}, "points": 0}
        series: dict[str, list[float]] = {}
        latest: dict[str, Any] = {}
        for row in rows:
            for key, value in row.items():
                if numeric(value):
                    series.setdefault(str(key), []).append(float(value))
                    latest[str(key)] = value
        return {"latest": latest, "series": series, "points": len(rows)}

    latest = {}
    series = {}
    for key, value in data.items():
        if numeric(value):
            latest[str(key)] = value
        elif isinstance(value, list) and value and all(numeric(item) for item in value):
            series[str(key)] = [float(item) for item in value]
            latest[str(key)] = value[-1]
    points = max((len(values) for values in series.values()), default=1)
    return {"latest": latest, "series": series, "points": points}


def read_remote_metrics(
    server: ServerConfig,
    path: str | None = None,
    *,
    timeout: float = 25,
) -> dict[str, Any]:
    """读取远端实验指标；未给 path 时自动找最新的 runs/*/metrics.json。"""
    executor = SSHExecutor(server)
    target = str(path or "").strip()
    if not target:
        discovery = METRICS_DISCOVERY.format(workdir=executor.workdir.rstrip("/"))
        found = executor.run_raw(discovery, timeout=timeout)
        candidates = [line.strip() for line in (found.stdout or "").splitlines() if line.strip()]
        if not candidates:
            return {
                "ok": False,
                "server": server.name,
                "error": (
                    "未在服务器找到 metrics.json。请确认训练脚本把指标写到 "
                    f"{executor.workdir}/runs/<run>/metrics.json，或手动指定 path。"
                ),
            }
        target = candidates[0]
    remote_path, warning = resolve_remote_path(target, executor.workdir, allow_outside=True)
    result = executor.run_raw(f"cat {quote_path(remote_path)}", timeout=timeout)
    if not result.ok:
        return {
            "ok": False,
            "server": server.name,
            "path": remote_path,
            "error": f"读取指标失败：{(result.stderr or result.stdout).strip()[:600]}",
        }
    parsed = parse_metrics_payload(result.stdout)
    return {
        "ok": "error" not in parsed,
        "server": server.name,
        "path": remote_path,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "warning": warning,
        "raw_preview": (result.stdout or "")[:3000],
        **parsed,
    }


class RemoteMetricsTool(BaseTool):
    name = "remote_metrics"
    description = (
        "读取 GPU 服务器上的实验指标（默认自动找最新 runs/*/metrics.json），返回最新数值与历史序列，"
        "用于实时监控训练进度。何时用：用户问「现在指标怎么样/训练到哪了/损失降了吗」，"
        "或需要根据实时期指标决定是否停止/调整实验时。"
    )
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "path": {
                "type": "string",
                "description": "远端 metrics.json 路径（相对 workdir 或绝对路径）；留空自动发现最新",
            },
        },
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        info = read_remote_metrics(server, kwargs.get("path"))
        if not info.get("ok"):
            return ToolResult.failure(str(info.get("error") or "读取指标失败"))
        latest = info.get("latest") or {}
        series = info.get("series") or {}
        lines = [f"## 实验指标：{info.get('path')}", "", f"- 服务器：{server.name}"]
        if latest:
            lines.append("")
            lines.append("| 指标 | 最新值 |")
            lines.append("|---|---|")
            for key, value in latest.items():
                lines.append(f"| {key} | {value} |")
        if series:
            lines.append("")
            lines.append(f"- 历史序列：{', '.join(f'{key}({len(values)} 点)' for key, values in series.items())}")
        return ToolResult.success(
            "\n".join(lines),
            data=info,
            display=f"{len(latest)} 个指标 · {info.get('points')} 个记录点",
        )


register_tool(RemoteMetricsTool())


class RemoteTailTool(BaseTool):
    name = "remote_tail"
    description = "查看 GPU 服务器上的训练日志（tail 最后 N 行），用于监控后台任务进度。"
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "log_path": {"type": "string", "description": "远端日志路径（相对 workdir 或绝对路径）"},
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "lines": {"type": "integer", "default": 100, "description": "行数（1-2000）"},
        },
        "required": ["log_path"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        log_path = str(self.require(kwargs, "log_path"))
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        executor = _executor_for(ctx, server)
        result = executor.tail_logs(log_path, lines=int(kwargs.get("lines") or 100))
        if not result.ok:
            return ToolResult.failure(
                f"读取日志失败：{(result.stderr or result.stdout).strip()[:500]}"
            )
        return ToolResult.success(
            f"[{server.name}] {log_path}\n{result.stdout.strip() or '（日志为空）'}",
            data={"server": server.name, "log_path": log_path, "lines": result.stdout.count("\n") + 1},
        )


class GpuStatusTool(BaseTool):
    name = "gpu_status"
    description = (
        "查看 GPU 状态：型号/显存/利用率/温度/进程（nvidia-smi 解析）。"
        "可指定 server，或 all=true 汇总所有服务器；未配置服务器时返回本机降级信息（macOS 无 nvidia-smi）。"
    )
    tags = ("remote", "experiment")
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名；缺省用第一个已配置服务器"},
            "all": {"type": "boolean", "default": False, "description": "查询所有已配置服务器"},
            "local": {"type": "boolean", "default": False, "description": "强制查询本机 GPU"},
            "with_processes": {"type": "boolean", "default": True, "description": "是否附带 GPU 进程"},
        },
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        servers = list(getattr(ctx.settings, "servers", []) or [])
        # 显式指定 server 但没配置时，报错而不是悄悄查本机
        use_local = bool(kwargs.get("local")) or (not servers and not kwargs.get("server"))
        if use_local:
            return self._local_status()
        targets = servers if kwargs.get("all") else [ctx.get_server(kwargs.get("server"))]
        targets = [s for s in targets if s is not None]
        if not targets:
            return ToolResult.failure(f"未找到服务器：{kwargs.get('server')}")
        blocks: list[str] = []
        data: dict[str, Any] = {"servers": []}
        any_ok = False
        for server in targets:
            executor = _executor_for(ctx, server)
            result = executor.run(NVIDIA_SMI_QUERY, timeout=30, wrap=False)
            if not result.ok:
                info = {
                    "server": server.name,
                    "available": False,
                    "error": (result.stderr or result.stdout or "ssh 失败").strip()[:500],
                }
                data["servers"].append(info)
                blocks.append(f"### {server.name}\n⚠️ 无法获取 GPU 状态：{info['error']}")
                continue
            gpus = parse_nvidia_smi(result.stdout)
            processes: list[dict[str, Any]] = []
            if kwargs.get("with_processes", True):
                proc_result = executor.run(NVIDIA_SMI_APPS_QUERY, timeout=30, wrap=False)
                if proc_result.ok:
                    processes = parse_nvidia_smi_apps(proc_result.stdout)
            any_ok = True
            data["servers"].append(
                {"server": server.name, "available": True, "gpus": gpus, "processes": processes}
            )
            block = format_gpu_table(gpus, title=f"{server.name} ({server.host})")
            if processes:
                proc_lines = [
                    f"- pid {p.get('pid')}: {p.get('process_name')} ({p.get('used_memory_mb')} MB)"
                    for p in processes
                ]
                block += "\n\n**进程**\n" + "\n".join(proc_lines)
            blocks.append(block)
        data["ok"] = any_ok
        content = "\n\n".join(blocks)
        if not any_ok:
            return ToolResult(ok=False, content=content, data=data, error="所有服务器 GPU 状态查询失败")
        return ToolResult.success(content, data=data)

    @staticmethod
    def _local_status() -> ToolResult:
        info = local_gpu_info()
        if info.get("available"):
            table = format_gpu_table(info["gpus"], title=f"本机 GPU（{info.get('platform')}）")
            return ToolResult.success(table, data=info)
        reason = info.get("reason", "本机没有可用的 NVIDIA GPU")
        content = f"本机 GPU：不可用\n{reason}"
        if info.get("mps"):
            content += "\n提示：Apple Silicon 可用 PyTorch MPS 后端（device='mps'）。"
        return ToolResult.success(content, data=info)


class RemoteJobsTool(BaseTool):
    name = "remote_jobs"
    description = "列出 GPU 服务器上的后台任务：tmux 会话/窗口 + Slurm 队列（scheduler=slurm 时）。"
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "all": {"type": "boolean", "default": False, "description": "查询所有已配置服务器"},
        },
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        servers = list(getattr(ctx.settings, "servers", []) or [])
        if not servers:
            return ToolResult.failure("尚未配置 GPU 服务器。先运行 `sciforge ssh add ...`。")
        targets = servers if kwargs.get("all") else [ctx.get_server(kwargs.get("server"))]
        targets = [s for s in targets if s is not None]
        if not targets:
            return ToolResult.failure(f"未找到服务器：{kwargs.get('server')}")
        blocks: list[str] = []
        data: dict[str, Any] = {}
        for server in targets:
            executor = _executor_for(ctx, server)
            sessions = executor.list_tmux_sessions()
            windows = executor.list_windows()
            slurm_jobs = executor.slurm_queue() if str(server.scheduler) == "slurm" else []
            data[server.name] = {"tmux_sessions": sessions, "tmux_windows": windows, "slurm": slurm_jobs}
            block = [f"### {server.name}"]
            if sessions:
                block.append("tmux 会话：" + "、".join(
                    f"{s['name']}({s.get('windows')}w)" if s.get("windows") is not None else s["name"]
                    for s in sessions
                ))
            else:
                block.append("tmux 会话：（无）")
            if windows:
                block.append("当前会话窗口：" + "、".join(windows))
            if str(server.scheduler) == "slurm":
                if slurm_jobs:
                    block.append("Slurm 队列：")
                    for job in slurm_jobs:
                        block.append(
                            f"- {job['job_id']} {job.get('name','')} [{job.get('state','')}] "
                            f"{job.get('time','')} {job.get('nodelist','')}"
                        )
                else:
                    block.append("Slurm 队列：（空）")
            blocks.append("\n".join(block))
        return ToolResult.success("\n\n".join(blocks), data=data)


class RemoteKillTool(BaseTool):
    name = "remote_kill"
    description = "停止 GPU 服务器上的后台任务（tmux 窗口/会话、nohup PID 或 Slurm job）。属于破坏性操作。"
    tags = ("remote", "experiment")
    requires = ("ssh",)
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "tmux 的 session:window、PID 或 Slurm job id"},
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "kind": {"type": "string", "enum": ["auto", "tmux", "slurm", "pid"], "default": "auto"},
        },
        "required": ["job_id"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        job_id = str(self.require(kwargs, "job_id"))
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        executor = _executor_for(ctx, server)
        result = executor.kill_job(job_id, kind=str(kwargs.get("kind") or "auto"))
        if not result.ok:
            return ToolResult.failure(
                f"停止 {job_id} 失败：{(result.stderr or result.stdout).strip()[:500]}"
            )
        return ToolResult.success(
            f"已停止 {server.name} 上的 {job_id}\n{(result.stdout or '').strip()}",
            data={**result.to_dict(), "server": server.name, "job_id": job_id},
        )


class SlurmSubmitTool(BaseTool):
    name = "slurm_submit"
    description = (
        "为 Slurm 集群生成 sbatch 脚本并提交一个后台训练任务，返回 Slurm job id 与日志路径。"
        "仅当 server.scheduler='slurm' 时可用。"
    )
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "训练命令，如 `bash scripts/run.sh`"},
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "name": {"type": "string", "description": "job 名/run_id，默认自动"},
            "partition": {"type": "string", "description": "分区（默认 server.partition）"},
            "gpus": {"type": "integer", "default": 1},
            "gpu_type": {"type": "string", "description": "如 a100 / 4090，默认 server.gpu_type"},
            "cpus": {"type": "integer", "default": 4},
            "mem": {"type": "string", "default": "32G"},
            "time": {"type": "string", "default": "24:00:00"},
        },
        "required": ["command"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        command = str(self.require(kwargs, "command"))
        try:
            ensure_safe_command(command)
        except PermissionError as exc:
            return ToolResult.failure(str(exc))
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        if str(server.scheduler) != "slurm":
            return ToolResult.failure(
                f"服务器 {server.name} 的 scheduler={server.scheduler}，不是 slurm。"
                "请用 remote_run(detached=true) 走 tmux/nohup。"
            )
        executor = _executor_for(ctx, server)
        name = str(kwargs.get("name") or f"job-{time.strftime('%Y%m%d-%H%M%S')}")
        run_dir = posixpath.join(executor.workdir, "runs", name)
        log_path = posixpath.join(run_dir, "slurm-%j.log")
        script = render_sbatch_script(
            job_name=name,
            command=command,
            workdir=executor.workdir,
            conda_env=server.conda_env,
            partition=str(kwargs.get("partition") or server.partition or "") or None,
            gpu_type=str(kwargs.get("gpu_type") or server.gpu_type or "") or None,
            gpus=int(kwargs.get("gpus") or 1),
            cpus=int(kwargs.get("cpus") or 4),
            mem=str(kwargs.get("mem") or "32G"),
            time=str(kwargs.get("time") or "24:00:00"),
            log_path=log_path,
            env=server.env,
        )
        try:
            job = executor.submit_slurm(script, run_id=name, log_path=log_path)
        except RuntimeError as exc:
            return ToolResult.failure(str(exc))
        content = (
            f"已提交 Slurm 任务 {name}\n"
            f"job_id: {job['job_id']}\n"
            f"脚本: {job['script_path']}\n"
            f"日志: {job['log_path']}\n"
            f"查看队列: `sciforge remote_jobs --server {server.name}`；日志: `sciforge remote_tail {job['log_path']} --server {server.name}`\n"
            f"取消: `sciforge remote_kill {job['job_id']} --server {server.name}`"
        )
        return ToolResult.success(content, data={**job, "server": server.name, "script": script})


class SlurmStatusTool(BaseTool):
    name = "slurm_status"
    description = "查询 Slurm 队列（squeue）与历史（sacct），返回 job 状态表。"
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "jobs": {"type": "array", "items": {"type": "string"}, "description": "只看指定 job id"},
            "history": {"type": "boolean", "default": True, "description": "是否附带 sacct 历史"},
        },
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        executor = _executor_for(ctx, server)
        jobs = [str(j) for j in (kwargs.get("jobs") or [])]
        queue = executor.slurm_queue()
        if jobs:
            queue = [job for job in queue if job.get("job_id") in jobs]
        history = executor.slurm_history(jobs=jobs or None) if kwargs.get("history", True) else []
        if jobs:
            history = [job for job in history if job.get("job_id") in jobs]
        lines: list[str] = [f"### {server.name} Slurm 队列"]
        if queue:
            lines.append("| JOBID | NAME | PARTITION | STATE | TIME | NODES | NODELIST |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for job in queue:
                lines.append(
                    "| {job_id} | {name} | {partition} | {state} | {time} | {nodes} | {nodelist} |".format(
                        **{k: job.get(k, "") for k in ("job_id", "name", "partition", "state", "time", "nodes", "nodelist")}
                    )
                )
        else:
            lines.append("（队列为空）")
        if history:
            lines += ["", "### 历史（sacct）", "| JOBID | NAME | STATE | EXIT | ELAPSED | END |", "| --- | --- | --- | --- | --- | --- |"]
            for job in history[-20:]:
                lines.append(
                    "| {job_id} | {name} | {state} | {exit_code} | {elapsed} | {end} |".format(
                        **{k: job.get(k, "") for k in ("job_id", "name", "state", "exit_code", "elapsed", "end")}
                    )
                )
        return ToolResult.success("\n".join(lines), data={"server": server.name, "queue": queue, "history": history})


class SlurmLogsTool(BaseTool):
    name = "slurm_logs"
    description = "读取 Slurm 任务日志（slurm-<jobid>.log），并附带 sacct 状态摘要。"
    tags = ("remote", "experiment")
    requires = ("ssh",)
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名（默认第一个）"},
            "log_path": {"type": "string", "description": "日志路径；省略时按最新 run 目录下的 slurm-*.log 查找"},
            "job_id": {"type": "string", "description": "Slurm job id（用于 sacct 摘要）"},
            "lines": {"type": "integer", "default": 100},
        },
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        server, failure = _server_or_fail(ctx, kwargs.get("server"), tool=self.name)
        if failure:
            return failure
        assert server is not None
        executor = _executor_for(ctx, server)
        log_path = str(kwargs.get("log_path") or "").strip()
        if not log_path:
            job_id = str(kwargs.get("job_id") or "").strip()
            found = executor.run_raw(
                f"ls -t {quote_path(posixpath.join(executor.workdir, 'runs'))}/*/slurm-*.log 2>/dev/null | head -n 1",
                timeout=20,
            )
            log_path = (found.stdout or "").strip().splitlines()[0] if found.stdout.strip() else ""
            if not log_path:
                return ToolResult.failure("未找到 slurm 日志，请显式传 log_path。")
            if job_id:
                log_path = log_path
        tail = executor.tail_logs(log_path, lines=int(kwargs.get("lines") or 100))
        history: list[dict[str, Any]] = []
        job_id = str(kwargs.get("job_id") or "").strip()
        if job_id:
            history = [j for j in executor.slurm_history(jobs=[job_id]) if j.get("job_id") == job_id]
        content = f"[{server.name}] {log_path}\n{tail.stdout.strip() or '（日志为空）'}"
        if history:
            content += "\n\nsacct: " + json.dumps(history[0], ensure_ascii=False)
        return ToolResult(
            ok=tail.ok,
            content=content,
            data={"server": server.name, "log_path": log_path, "history": history},
            error=None if tail.ok else (tail.stderr.strip()[:500] or None),
        )


register_tool(RemoteRunTool())
register_tool(RemoteSyncTool())
register_tool(RemoteTailTool())
register_tool(GpuStatusTool())
register_tool(RemoteJobsTool())
register_tool(RemoteKillTool())
register_tool(SlurmSubmitTool())
register_tool(SlurmStatusTool())
register_tool(SlurmLogsTool())


# ---------------------------------------------------------------------------
# CLI（由 Lead 的 cli.py 调 add_cli(app)）
# ---------------------------------------------------------------------------


def _make_ctx(project_dir: Path | None = None) -> ToolContext:
    """构造 CLI 用的 ToolContext。"""
    from sciforge.config import data_home, find_project_root

    project = Path(project_dir).expanduser().resolve() if project_dir else (find_project_root() or Path.cwd())
    settings = get_settings(project)
    data_dir = data_home()
    return ToolContext(
        settings=settings,
        project_dir=project,
        data_dir=data_dir,
        session_dir=project / ".sciforge" / "sessions" / "cli",
    )


def _json_out(payload: Any) -> None:
    import typer

    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _console():
    from rich.console import Console

    return Console()


def _fail(message: str, *, json_mode: bool = False, code: int = 1):
    import typer

    if json_mode:
        _json_out({"ok": False, "error": message})
    else:
        _console().print(f"[red]✗ {message}[/red]")
    raise typer.Exit(code=code)


def _server_table(servers: Sequence[ServerConfig]) -> None:
    from rich.table import Table

    table = Table(title="GPU 服务器", show_lines=False)
    for column in ("name", "host", "user", "scheduler", "workdir", "conda_env"):
        table.add_column(column)
    for server in servers:
        table.add_row(
            server.name,
            f"{server.host}:{server.port}",
            server.user,
            str(server.scheduler),
            str(server.workdir),
            str(server.conda_env or "-"),
        )
    _console().print(table)


def _add_typer_once(app: Any, name: str, builder: Any) -> None:
    """幂等挂载 Typer 子 app（防止 remote/experiments 互相调用时重复挂载）。"""
    attached = getattr(app, "_sciforge_t2_attached", None)
    if attached is None:
        attached = set()
        try:
            app._sciforge_t2_attached = attached  # noqa: B010 - Typer 对象可动态挂属性
        except (AttributeError, TypeError):  # pragma: no cover - 非 Typer 对象
            pass
    if name in attached:
        return
    attached.add(name)
    app.add_typer(builder(), name=name)


def build_ssh_app() -> Any:
    import typer

    app = typer.Typer(help="SSH 服务器管理：add / list / test / sync / exec", no_args_is_help=True)

    @app.command("add")
    def ssh_add(
        name: str = typer.Argument(..., help="服务器名，如 gpu1"),
        host: str = typer.Option("", "--host", help="主机名或 IP；可用 --from-command 自动解析"),
        user: str = typer.Option("root", "--user"),
        port: int = typer.Option(22, "--port"),
        key: str = typer.Option(None, "--key", help="identity_file 私钥路径"),
        password: str = typer.Option(None, "--password", help="SSH 密码（仅存本机 0600；更推荐 Key）"),
        from_command: str = typer.Option(None, "--from-command", help="粘贴 ssh 登录命令，自动解析 host/port/user"),
        workdir: str = typer.Option("~/sciforge", "--workdir", help="远端工作目录"),
        conda_env: str = typer.Option(None, "--conda-env"),
        tmux_session: str = typer.Option("sciforge", "--tmux-session"),
        scheduler: str = typer.Option("none", "--scheduler", help="none | slurm"),
        partition: str = typer.Option(None, "--partition"),
        proxy_jump: str = typer.Option(None, "--proxy-jump", help="跳板 user@host"),
        json_mode: bool = typer.Option(False, "--json", help="机器可读输出"),
    ) -> None:
        """新增/更新一台 GPU 服务器到 ~/.sciforge/config.toml。"""
        from sciforge.config import save_global_config

        settings = get_settings()
        existing = settings.server(name)
        if from_command:
            try:
                parsed = parse_ssh_command(from_command)
            except ValueError as exc:
                _fail(str(exc), json_mode=json_mode)
            host = host or parsed["host"]
            port = int(port) if "--port" in sys.argv else int(parsed["port"] or 22)
            user = user if user != "root" else (parsed["user"] or user)
            key = key or parsed.get("identity_file")
            proxy_jump = proxy_jump or parsed.get("proxy_jump")
        if not host:
            _fail("缺少 host：请用 --host，或用 --from-command 'ssh -p 端口 user@host'", json_mode=json_mode)
        server = ServerConfig(
            name=name,
            host=host,
            port=int(port),
            user=user,
            identity_file=key,
            password=(password if password is not None else (existing.password if existing else None)),
            workdir=workdir,
            conda_env=conda_env,
            tmux_session=tmux_session,
            scheduler=scheduler,
            partition=partition,
            proxy_jump=proxy_jump,
        )
        settings.servers = [s for s in settings.servers if s.name != name] + [server]
        try:
            path = save_global_config(settings)
        except Exception as exc:  # noqa: BLE001
            _fail(f"保存配置失败：{exc}", json_mode=json_mode)
        payload = {"ok": True, "config": str(path), "server": server.model_dump(mode="json")}
        if json_mode:
            _json_out(payload)
            return
        _console().print(f"[green]✓ 已保存服务器 {name} → {path}[/green]")
        _console().print(f"下一步：`sciforge ssh test {name}`")

    @app.command("list")
    def ssh_list(json_mode: bool = typer.Option(False, "--json")) -> None:
        """列出已配置的服务器。"""
        settings = get_settings()
        if not settings.servers:
            if json_mode:
                _json_out({"servers": []})
            else:
                _console().print("尚未配置服务器。`sciforge ssh add gpu1 --host 1.2.3.4 --user root`")
            return
        if json_mode:
            _json_out({"servers": [s.model_dump(mode="json") for s in settings.servers]})
            return
        _server_table(settings.servers)

    @app.command("test")
    def ssh_test(
        name: str = typer.Argument(None, help="服务器名（默认第一个）"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """测试 SSH 连通性并探测远端环境（uname/python/torch/GPU/tmux/conda）。"""
        ctx = _make_ctx()
        server, failure = _server_or_fail(ctx, name, tool="ssh test")
        if failure:
            _fail(failure.error or failure.content, json_mode=json_mode)
        assert server is not None
        info = SSHExecutor(server).check()
        if json_mode:
            _json_out(info)
            if not info.get("reachable"):
                raise typer.Exit(1)
            return
        if not info.get("reachable"):
            _fail(f"连接失败：{info.get('error')}", json_mode=False)
        from rich.table import Table

        table = Table(title=f"✓ {server.name} 连通")
        table.add_column("项目")
        table.add_column("值")
        for key in ("os", "hostname", "user", "python", "torch", "cuda", "gpu_count", "gpu_names", "tmux", "conda", "workdir"):
            value = info.get(key)
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "-"
            table.add_row(key, str(value or "-"))
        _console().print(table)

    @app.command("sync")
    def ssh_sync(
        server: str = typer.Argument(..., help="服务器名"),
        local: str = typer.Argument(..., help="本地路径（upload）或本地目录（download）"),
        remote: str = typer.Argument(..., help="远端目录（upload）或远端路径（download）"),
        direction: str = typer.Option("upload", "--direction", help="upload | download"),
        exclude: str = typer.Option(None, "--exclude", help="排除模式，可重复；如 data --exclude .git"),
        delete: bool = typer.Option(False, "--delete", help="rsync --delete（谨慎）"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """本地 ↔ 服务器同步（rsync 优先，scp 兜底；自动套用 server.sync_excludes）。"""
        ctx = _make_ctx()
        server_obj = ctx.get_server(server)
        if server_obj is None:
            _fail(f"未找到服务器 {server}（先 `sciforge ssh add`）", json_mode=json_mode)
        executor = SSHExecutor(server_obj)
        excludes = [exclude] if exclude else []
        try:
            if direction.lower() == "download":
                result = executor.download_paths(
                    [remote], Path(local).expanduser(), delete=delete, excludes=excludes
                )
            else:
                local_path = Path(local).expanduser()
                if not local_path.exists():
                    _fail(f"本地路径不存在：{local_path}", json_mode=json_mode)
                source = str(local_path) + ("/" if local_path.is_dir() and local.endswith("/") else "")
                if local_path.is_dir() and not local.endswith("/"):
                    source += "/"
                result = executor.upload_paths([source], remote, delete=delete, excludes=excludes)
        except PermissionError as exc:
            _fail(str(exc), json_mode=json_mode)
            raise
        payload = {"ok": result.ok, "server": server, "direction": direction, **result.to_dict()}
        if json_mode:
            _json_out(payload)
            if not result.ok:
                raise typer.Exit(1)
            return
        if result.ok:
            _console().print(f"[green]✓ 同步完成（{direction}）：{local} ↔ {server}:{remote}[/green]")
            if (result.stdout or "").strip():
                _console().print((result.stdout or "").strip()[-2000:])
        else:
            _fail(f"同步失败（rc={result.rc}）：{(result.stderr or result.stdout).strip()[:800]}")

    @app.command("exec")
    def ssh_exec(
        name: str = typer.Argument(..., help="服务器名"),
        command: str = typer.Argument(..., help="要执行的命令"),
        timeout: int = typer.Option(120, "--timeout"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """在服务器上执行一条短命令。"""
        try:
            ensure_safe_command(command)
        except PermissionError as exc:
            _fail(str(exc), json_mode=json_mode)
        ctx = _make_ctx()
        server = ctx.get_server(name)
        if server is None:
            _fail(f"未找到服务器 {name}", json_mode=json_mode)
        executor = SSHExecutor(server)
        result = executor.run(command, timeout=timeout)
        if json_mode:
            _json_out({**result.to_dict(), "server": name})
            if not result.ok:
                raise typer.Exit(1)
            return
        if result.stdout.strip():
            _console().print(result.stdout.rstrip())
        if result.stderr.strip():
            _console().print(f"[yellow]{result.stderr.rstrip()}[/yellow]")
        if not result.ok:
            _fail(f"命令退出码 {result.rc}", json_mode=False)
        _console().print(f"[green]exit={result.rc} ({result.duration_s:.2f}s)[/green]")

    return app


def build_gpu_app() -> Any:
    import typer

    app = typer.Typer(help="GPU 状态：nvidia-smi 解析 / 多服务器 / 本地降级", no_args_is_help=True)

    @app.command("status")
    def gpu_status(
        server: str = typer.Argument(None, help="服务器名（默认第一个）"),
        all_servers: bool = typer.Option(False, "--all", help="查询所有服务器"),
        local: bool = typer.Option(False, "--local", help="强制查询本机 GPU"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """显示 GPU 型号/显存/利用率/温度/进程。"""
        ctx = _make_ctx()
        tool = GpuStatusTool()
        result = tool.run(ctx, server=server, all=all_servers, local=local)
        if json_mode:
            _json_out({"ok": result.ok, **result.data})
            if not result.ok:
                raise typer.Exit(1)
            return
        if result.ok:
            _console().print(result.content)
        else:
            _fail(result.error or result.content, json_mode=False)

    return app


def add_cli(app: Any) -> None:
    """把 `ssh` / `gpu`（以及 `exp`）子命令挂到根 Typer app 上。"""
    _add_typer_once(app, "ssh", build_ssh_app)
    _add_typer_once(app, "gpu", build_gpu_app)
    try:
        from sciforge.tools.experiments import add_cli as _add_exp_cli

        _add_exp_cli(app)
    except Exception:  # noqa: BLE001 - 实验模块不可用时不影响 ssh/gpu
        pass


__all__ = [
    "SSHExecutor",
    "parse_ssh_command",
    "LocalExecutor",
    "RemoteResult",
    "make_executor",
    "parse_nvidia_smi",
    "parse_nvidia_smi_apps",
    "parse_squeue",
    "parse_sacct",
    "parse_tmux_sessions",
    "parse_sbatch_output",
    "parse_check_output",
    "parse_metrics_payload",
    "read_remote_metrics",
    "is_dangerous_command",
    "ensure_safe_command",
    "resolve_remote_path",
    "wrap_remote_command",
    "conda_activate_script",
    "render_sbatch_script",
    "local_gpu_info",
    "format_gpu_table",
    "add_cli",
]
