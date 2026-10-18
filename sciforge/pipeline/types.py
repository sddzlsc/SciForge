"""Pipeline 的纯数据模型（避免 engine 与 recipes 循环依赖）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Stage:
    name: str
    title: str
    prompt: str  # prompts/stages/<prompt>.md
    tools: list[str] = field(default_factory=list)
    role: str = "executor"
    max_steps: int = 12
    artifacts: list[str] = field(default_factory=list)  # 期望的文件名（相对 stage 目录）


@dataclass
class Recipe:
    name: str
    title: str
    description: str
    stages: list[Stage]
    tags: tuple[str, ...] = ()


@dataclass
class StageResult:
    name: str
    title: str
    ok: bool
    content: str = ""
    artifacts: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration: float = 0.0
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "ok": self.ok,
            "artifacts": self.artifacts,
            "usage": self.usage,
            "error": self.error,
            "duration": round(self.duration, 2),
            "skipped": self.skipped,
        }


@dataclass
class PipelineRun:
    run_id: str
    recipe: str
    topic: str
    run_dir: Path
    status: str = "running"
    stages: list[StageResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "done" and all(s.ok or s.skipped for s in self.stages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "recipe": self.recipe,
            "topic": self.topic,
            "run_dir": str(self.run_dir),
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "stages": [s.to_dict() for s in self.stages],
        }



__all__ = ["Stage", "Recipe", "StageResult", "PipelineRun"]
