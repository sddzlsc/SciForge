#!/usr/bin/env python
"""收集可复现性环境快照（纯标准库；不联网，可选记录 git 状态）。

用法::

    python collect_env.py                       # 打印 JSON 到 stdout
    python collect_env.py -o repro/env.json     # 写入文件
    python collect_env.py --repo . --full       # 额外收集 pip freeze / git 信息

输出包含：Python/平台/CPU/内存、关键包版本、环境变量中的 CUDA 相关项、
指定仓库的 git commit/branch/status，以及可选的 pip freeze。
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

KEY_PACKAGES = (
    "torch",
    "torchvision",
    "numpy",
    "scipy",
    "transformers",
    "datasets",
    "accelerate",
    "deepspeed",
    "vllm",
    "lightning",
    "pytorch-lightning",
    "timm",
    "wandb",
    "matplotlib",
    "scikit-learn",
    "jax",
    "tensorflow",
)
CUDA_ENV_KEYS = ("CUDA_VISIBLE_DEVICES", "CUDA_HOME", "CUDA_PATH", "NVIDIA_VISIBLE_DEVICES")


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in KEY_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _run(command: list[str], cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip()


def _git_info(repo: Path) -> dict[str, str]:
    info = {
        "commit": _run(["git", "rev-parse", "HEAD"], cwd=repo),
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo),
        "dirty": _run(["git", "status", "--porcelain"], cwd=repo) != "",
    }
    remote = _run(["git", "config", "--get", "remote.origin.url"], cwd=repo)
    if remote:
        info["remote"] = remote
    return info


def collect(*, repo: Path | None = None, full: bool = False) -> dict:
    payload: dict = {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "cwd": str(Path.cwd()),
        "packages": _package_versions(),
        "cuda_env": {key: os.environ[key] for key in CUDA_ENV_KEYS if key in os.environ},
    }
    if shutil.which("nvidia-smi"):
        payload["nvidia_smi"] = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    if repo is not None:
        payload["git"] = _git_info(repo)
    if full:
        payload["pip_freeze"] = _run([sys.executable, "-m", "pip", "freeze"]).splitlines()
        payload["nvidia_smi_full"] = _run(["nvidia-smi"])
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="收集可复现性环境快照")
    parser.add_argument("--repo", type=Path, default=None, help="收集 git 信息的仓库目录")
    parser.add_argument("-o", "--output", type=Path, default=None, help="写入文件（默认打印）")
    parser.add_argument("--full", action="store_true", help="额外收集 pip freeze 与完整 nvidia-smi")
    args = parser.parse_args(argv)

    repo = args.repo
    if repo is not None and not (repo / ".git").exists() and not (repo / "pyproject.toml").exists():
        print(f"[警告] {repo} 看起来不是仓库目录，仍会记录路径信息", file=sys.stderr)

    payload = collect(repo=repo, full=args.full)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
