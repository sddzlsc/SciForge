"""配置加载与合并测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.config import (
    ConfigError,
    data_home,
    dump_toml,
    get_settings,
    save_project_config,
)


def test_default_settings(sciforge_home: Path) -> None:
    settings = get_settings()
    assert settings.llm.default == "deepseek"
    assert settings.llm.providers["deepseek"].default_model == "deepseek-flash"
    assert settings.llm.roles["executor"].model == "deepseek-flash"
    assert settings.llm.providers["deepseek"].base_url.startswith("https://api.deepseek.com")
    assert settings.llm.roles["planner"].model == "deepseek-v4-pro"
    assert settings.literature.daily_arxiv_language == "Chinese"
    assert data_home() == sciforge_home


def test_global_and_project_merge(sciforge_home: Path, project: Path) -> None:
    (sciforge_home / "config.toml").write_text(
        """
[llm]
default = "deepseek"

[llm.providers.custom]
type = "openai-compatible"
base_url = "http://127.0.0.1:8000/v1"
api_key = "sk-test"
default_model = "local-model"

[figures]
style = "cvpr"
""",
        encoding="utf-8",
    )
    (project / ".sciforge" / "project.toml").write_text(
        """
project_name = "my-research"

[writing]
venue = "neurips"
""",
        encoding="utf-8",
    )
    settings = get_settings(project)
    assert settings.project_name == "my-research"
    assert settings.figures.style == "cvpr"
    assert settings.writing.venue == "neurips"
    assert settings.llm.providers["custom"].default_model == "local-model"
    assert settings.resolve_api_key("custom") == "sk-test"


def test_env_api_key(sciforge_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    settings = get_settings()
    assert settings.resolve_api_key("deepseek") == "sk-from-env"


def test_missing_provider_raises(sciforge_home: Path) -> None:
    settings = get_settings()
    with pytest.raises(ConfigError):
        settings.resolved_provider("nope")


def test_save_project_config_excludes_secrets(sciforge_home: Path, project: Path) -> None:
    settings = get_settings(project)
    settings.llm.providers["deepseek"].api_key = "sk-secret"
    save_project_config(project, settings)
    text = (project / ".sciforge" / "project.toml").read_text(encoding="utf-8")
    assert "sk-secret" not in text
    assert "project_name" in text


def test_dump_toml_roundtrip(tmp_path: Path) -> None:
    import sys

    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib

    data = {"a": 1, "b": "x", "c": True, "d": [1, 2], "e": {"f": "g"}}
    text = dump_toml(data)
    parsed = tomllib.loads(text)
    assert parsed == data


def test_global_config_roundtrip_with_servers(sciforge_home: Path) -> None:
    """save_global_config 写出的 TOML 必须能被重新读取（ssh add 依赖）。"""
    from sciforge.config import ServerConfig, save_global_config

    settings = get_settings()
    settings.servers.append(
        ServerConfig(name="gpu1", host="10.0.0.2", user="root", workdir="~/sciforge", conda_env="torch")
    )
    path = save_global_config(settings)
    text = path.read_text(encoding="utf-8")
    assert "null" not in text.lower()
    assert '""' not in text.split("[llm")[0]  # 服务器字段不应出现空字符串占位

    reloaded = get_settings()
    assert len(reloaded.servers) == 1
    server = reloaded.servers[0]
    assert server.name == "gpu1"
    assert server.host == "10.0.0.2"
    assert server.conda_env == "torch"
    assert reloaded.llm.roles["planner"].model == "deepseek-v4-pro"
