"""共享测试夹具：隔离的 SCIFORGE_HOME 与临时项目目录。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def sciforge_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sciforge-home"
    home.mkdir()
    monkeypatch.setenv("SCIFORGE_HOME", str(home))
    # 避免环境里的真实 key 影响测试
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "S2_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture()
def project(tmp_path: Path, sciforge_home: Path) -> Path:
    proj = tmp_path / "research-project"
    proj.mkdir()
    (proj / ".sciforge").mkdir()
    return proj
