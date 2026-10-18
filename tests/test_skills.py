"""T4 Skill 体系测试：解析 / lint / 搜索 / 安装 / DSH 目标 / CLI / 外部目录。

所有测试离线：不联网、不写真实 ``~/.dsh`` / ``~/.sciforge``。
隔离方式：
- 每个测试设 ``SCIFORGE_HOME`` 与 ``HOME`` 到 tmp_path；
- 需要落盘的安装测试一律使用 ``settings.skills.targets`` 指向 tmp_path；
- GitHub 安装只测 URL 解析与「多 skill 需指定 name」的逻辑（monkeypatch 掉 clone）。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

import sciforge.tools.skills  # noqa: F401  # 导入即注册 skill_* 工具
from sciforge.config import Settings, SkillsConfig
from sciforge.core.registry import ToolContext, get_tool, run_tool
from sciforge.skills_manager import (
    MAX_SCORE,
    PASS_SCORE,
    SkillError,
    SkillInstallError,
    SkillManager,
    SkillNotFoundError,
    SkillTargetError,
    is_kebab_case,
    normalize_skill_name,
    parse_github_source,
    parse_skill_md,
    split_frontmatter,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

GOOD_BODY = """# Demo Skill

## 何时使用（触发条件）

当需要演示 skill 解析、lint 与安装流程时使用；不适用于真实科研任务。

## 输入 / 输出

- 输入：一个演示任务。
- 输出：一份演示结果。

## 工作流

1. 读取输入并校验。
2. 执行演示步骤。
3. 输出结果并记录。

## 质量检查清单

- [ ] 输入已校验。
- [ ] 结果已记录。

## 失败处理

失败时回滚并记录错误日志，不要静默吞掉异常。

## SciForge 工具与 CLI 对应

| 步骤 | 工具 |
|---|---|
| 列出技能 | `skill_list`、`sciforge skills list` |

## 示例

```bash
sciforge skills list
```

## 进一步阅读

详细说明见 `references/notes.md`。
"""


def _frontmatter(name: str, description: str = "当需要演示技能时使用；产出演示结果。", **extra: object) -> str:
    payload: dict[str, object] = {
        "name": name,
        "description": description,
        "version": "0.1.0",
        "license": "MIT",
        "compatibility": "sciforge>=0.1",
        "metadata": {"author": "Test", "tags": ["experiment"]},
    }
    payload.update(extra)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def make_skill(
    root: Path,
    name: str = "demo-skill",
    *,
    body: str = GOOD_BODY,
    frontmatter: str | None = None,
    description: str = "当需要演示技能时使用；产出演示结果。",
    create_refs: bool = True,
) -> Path:
    """在 root 下创建一个合法 skill 目录。"""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = frontmatter if frontmatter is not None else _frontmatter(name, description)
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}---\n{body}", encoding="utf-8")
    if create_refs:
        refs = skill_dir / "references"
        refs.mkdir(exist_ok=True)
        (refs / "notes.md").write_text("# Notes\n\n演示参考文档。\n", encoding="utf-8")
    return skill_dir


def make_settings(tmp_path: Path, *, extra_dirs: list[Path] | None = None) -> Settings:
    """构造隔离的 Settings：dsh/claude/codex 目标全部指向 tmp_path。"""
    settings = Settings()
    settings.project_dir = tmp_path / "project"
    settings.skills = SkillsConfig(
        extra_dirs=[str(path) for path in (extra_dirs or [])],
        targets={
            "dsh": str(tmp_path / "agent-targets" / "dsh" / "skills"),
            "claude": str(tmp_path / "agent-targets" / "claude" / "skills"),
            "codex": str(tmp_path / "agent-targets" / "codex" / "skills"),
        },
    )
    return settings


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """确保任何默认路径都落在 tmp_path（不碰真实 HOME）。"""
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "sciforge-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


# ---------------------------------------------------------------------------
# frontmatter 解析
# ---------------------------------------------------------------------------


def test_split_frontmatter_basic_and_crlf() -> None:
    text = "---\nname: demo\ndescription: hi\n---\n# Body\n\ncontent"
    meta, body, error = split_frontmatter(text)
    assert error is None
    assert meta == {"name": "demo", "description": "hi"}
    assert body.startswith("# Body")

    crlf = text.replace("\n", "\r\n")
    meta_crlf, body_crlf, error_crlf = split_frontmatter(crlf)
    assert error_crlf is None
    assert meta_crlf["name"] == "demo"
    assert body_crlf.startswith("# Body")


def test_split_frontmatter_missing_and_broken() -> None:
    meta, body, error = split_frontmatter("# 无 frontmatter\n正文")
    assert meta == {}
    assert error is None
    assert body.startswith("# 无 frontmatter")

    meta, _body, error = split_frontmatter("---\nname: x\n# 没有结束标记")
    assert meta == {}
    assert error is not None

    meta, _body, error = split_frontmatter("---\n[1, 2, 3]\n---\nbody")
    assert meta == {}
    assert error is not None


def test_parse_skill_md(tmp_path: Path) -> None:
    skill_dir = make_skill(tmp_path / "collection", "demo-skill")
    parsed = parse_skill_md(skill_dir / "SKILL.md")
    assert parsed.frontmatter_ok is True
    assert parsed.meta["name"] == "demo-skill"
    assert "工作流" in parsed.body
    with pytest.raises(SkillNotFoundError):
        parse_skill_md(tmp_path / "nope" / "SKILL.md")


# ---------------------------------------------------------------------------
# bundled 技能库健康度
# ---------------------------------------------------------------------------


REQUIRED_SKILLS = {
    "sciforge-harness",
    "literature-survey",
    "paper-deep-read",
    "innovation-mining",
    "experiment-design",
    "remote-gpu-training",
    "training-debugging",
    "paper-figures-architecture",
    "paper-figures-results",
    "paper-writing",
    "reviewer-response",
    "latex-venue-submission",
    "reproducibility-package",
    "daily-arxiv-digest",
    "paper-retrieval-oa",
    "benchmark-eval-protocol",
    "slide-talk",
    "dataset-audit",
    "idea-novelty-check",
    "autoresearch-loop",
    "research-ideation",
    "ml-paper-writing",
    "academic-plotting",
    "distributed-training",
}


def test_bundled_skills_discovered_and_healthy(tmp_path: Path) -> None:
    manager = SkillManager(make_settings(tmp_path))
    skills = manager.list()
    names = {skill.name for skill in skills}
    assert len(skills) >= 16
    assert REQUIRED_SKILLS <= names
    assert all(skill.source == "bundled" for skill in skills)

    for skill in skills:
        assert skill.version, f"{skill.name} 缺 version"
        assert skill.license, f"{skill.name} 缺 license"
        assert skill.compatibility, f"{skill.name} 缺 compatibility"
        assert skill.tags, f"{skill.name} 缺 tags"
        assert skill.description
        parsed = parse_skill_md(skill.path)
        assert parsed.frontmatter_ok and parsed.meta.get("name") == skill.name
        assert len(parsed.body) < 5000, f"{skill.name} 正文超长: {len(parsed.body)}"
        report = manager.lint(skill.name)
        assert report["ok"], f"{skill.name} lint 未通过: {report['issues']}"
        assert PASS_SCORE <= report["score"] <= MAX_SCORE
        assert report["score"] >= 28, f"{skill.name} 质量分偏低: {report['score']}"

    assert manager.get("sciforge-harness") is not None
    assert manager.search("配图 架构图")[0].name in {
        "paper-figures-architecture",
        "academic-plotting",
        "paper-figures-results",
    }


def test_bundled_skill_references_are_discoverable(tmp_path: Path) -> None:
    manager = SkillManager(make_settings(tmp_path))
    resources = manager.resources("literature-survey")
    assert "references/search-strategies.md" in resources
    assert "检索式" in manager.read_resource("literature-survey", "references/search-strategies.md")
    text = manager.load("literature-survey")
    assert text.startswith("---")
    assert "name: literature-survey" in text


# ---------------------------------------------------------------------------
# lint 规则
# ---------------------------------------------------------------------------


def test_lint_perfect_skill_scores_max(tmp_path: Path) -> None:
    skill_dir = make_skill(tmp_path / "collection", "demo-skill")
    report = SkillManager(make_settings(tmp_path)).lint_dir(skill_dir)
    assert report["score"] == MAX_SCORE == 30
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["max_score"] == MAX_SCORE


def test_lint_flags_bad_skill(tmp_path: Path) -> None:
    bad_body = "# 有些内容\n\n" + "这是一段很长的正文。" * 600  # > 5000 字符
    frontmatter = "name: wrong-name\ndescription: 一个技能\nversion: 0.1.0\n"
    skill_dir = make_skill(
        tmp_path / "collection",
        "bad-skill",
        body=bad_body,
        frontmatter=frontmatter,
        create_refs=False,
    )
    (skill_dir / "README.md").write_text("多余 README", encoding="utf-8")

    report = SkillManager(make_settings(tmp_path)).lint_dir(skill_dir)
    assert report["ok"] is False
    assert report["score"] < PASS_SCORE
    assert report["score"] <= MAX_SCORE
    text = "\n".join(report["issues"])
    assert "name" in text and "不一致" in text
    assert "license" in text
    assert "README" in text
    assert "正文" in text  # 超长正文
    assert "references" in text or "scripts" in text


def test_lint_critical_when_skill_md_missing(tmp_path: Path) -> None:
    skill_dir = tmp_path / "collection" / "empty-skill"
    skill_dir.mkdir(parents=True)
    report = SkillManager(make_settings(tmp_path)).lint_dir(skill_dir)
    assert report["ok"] is False
    assert report["score"] < PASS_SCORE
    assert any(issue.startswith("[critical]") for issue in report["issues"])


# ---------------------------------------------------------------------------
# 发现 / 搜索 / 用户覆盖
# ---------------------------------------------------------------------------


def test_extra_dirs_external_and_search(tmp_path: Path) -> None:
    collection = tmp_path / "collection"
    make_skill(collection, "demo-skill")
    make_skill(collection, "another-skill", description="当需要另一个任务时使用；产出别的结果。")
    settings = make_settings(tmp_path, extra_dirs=[collection])
    manager = SkillManager(settings)

    external = manager.list(source="external")
    assert {skill.name for skill in external} == {"demo-skill", "another-skill"}
    assert manager.get("demo-skill").source == "external"
    assert manager.get("Demo-Skill").name == "demo-skill"  # 大小写不敏感
    assert manager.get("no-such-skill") is None

    assert manager.search("demo-skill")[0].name == "demo-skill"
    assert [skill.name for skill in manager.search("experiment")] != []
    assert manager.search("完全不存在的检索词xyz") == []
    assert len(manager.search("技能", limit=1)) == 1


def test_load_and_resource_traversal_guard(tmp_path: Path) -> None:
    collection = tmp_path / "collection"
    make_skill(collection, "demo-skill")
    manager = SkillManager(make_settings(tmp_path, extra_dirs=[collection]))
    assert manager.load("demo-skill").startswith("---")
    assert "$" not in manager.load("demo-skill")  # 占位符不存在
    with pytest.raises(SkillNotFoundError):
        manager.load("missing-skill")
    with pytest.raises(SkillNotFoundError):
        manager.read_resource("demo-skill", "references/missing.md")
    with pytest.raises(SkillError):
        manager.read_resource("demo-skill", "../../../etc/passwd")


def test_user_project_skill_overrides_bundled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    project_dir = settings.project_dir
    assert project_dir is not None
    user_skill = project_dir / ".sciforge" / "skills" / "literature-survey"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text(
        "---\nname: literature-survey\ndescription: 用户自定义覆盖版本\nversion: 9.9.9\n---\n# 覆盖\n",
        encoding="utf-8",
    )
    manager = SkillManager(settings)
    override = manager.get("literature-survey")
    assert override is not None
    assert override.source == "user"
    assert override.description == "用户自定义覆盖版本"
    # bundled 里同名 skill 仍然只有一条（被 user 覆盖）
    assert sum(1 for skill in manager.list() if skill.name == "literature-survey") == 1
    # 其他 bundled skill 不受影响
    assert manager.get("paper-writing").source == "bundled"


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def test_install_local_dir_into_installed(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source", "demo-skill")
    (source / "scripts").mkdir()
    (source / "scripts" / "helper.py").write_text("print('ok')\n", encoding="utf-8")

    settings = make_settings(tmp_path)
    manager = SkillManager(settings)
    installed = manager.install(str(source))

    assert len(installed) == 1
    dest = installed[0]
    assert dest == manager.installed_dir / "demo-skill"
    assert (dest / "SKILL.md").is_file()
    assert (dest / "references" / "notes.md").is_file()
    assert (dest / "scripts" / "helper.py").is_file()
    assert manager.get("demo-skill").source == "installed"
    assert any("已安装" in message for message in manager.last_install_messages)


def test_install_single_skill_md_and_zip(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source", "demo-skill")
    manager = SkillManager(make_settings(tmp_path))

    installed = manager.install(str(source / "SKILL.md"))
    assert installed[0].name == "demo-skill"

    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as bundle:
        for file in source.rglob("*"):
            if file.is_file():
                bundle.write(file, Path("pack") / file.relative_to(source))
    installed_zip = manager.install(str(zip_path))
    assert len(installed_zip) == 1
    assert (installed_zip[0] / "references" / "notes.md").is_file()


def test_install_unknown_source_raises(tmp_path: Path) -> None:
    manager = SkillManager(make_settings(tmp_path))
    with pytest.raises(SkillInstallError) as excinfo:
        manager.install("https://example.com/not-a-github-skill")
    assert "无法识别" in str(excinfo.value)
    with pytest.raises(SkillInstallError):
        manager.install(str(tmp_path / "不存在的目录"))


def test_install_github_url_uses_shallow_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """用假 git 验证 GitHub 安装路径（不联网）：浅克隆命令 + tree 子目录定位。"""
    import shutil
    from types import SimpleNamespace

    collection = tmp_path / "repo-content"
    make_skill(collection, "demo-skill")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        calls.append(list(command))
        clone_dir = Path(command[-1])
        (clone_dir / "skills").mkdir(parents=True, exist_ok=True)
        shutil.copytree(collection / "demo-skill", clone_dir / "skills" / "demo-skill")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("sciforge.skills_manager.subprocess.run", fake_run)
    manager = SkillManager(make_settings(tmp_path))
    installed = manager.install(
        "https://github.com/owner/repo/tree/main/skills/demo-skill", target="dsh"
    )
    assert len(installed) == 1
    assert installed[0].name == "demo-skill"
    assert (installed[0] / "SKILL.md").is_file()
    clone_command = calls[0]
    assert clone_command[:4] == ["git", "clone", "--depth", "1"]
    assert "--single-branch" in clone_command
    assert clone_command[-2] == "https://github.com/owner/repo.git"


def test_install_github_requires_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sciforge.skills_manager.shutil.which", lambda name: None)
    manager = SkillManager(make_settings(tmp_path))
    with pytest.raises(SkillInstallError) as excinfo:
        manager.install("fakeowner/fakerepo-xyz")
    assert "git" in str(excinfo.value)


def test_install_github_source_parsing() -> None:
    assert parse_github_source("owner/repo").owner == "owner"
    source = parse_github_source("https://github.com/owner/repo")
    assert source is not None and source.repo == "repo"
    tree = parse_github_source("https://github.com/owner/repo/tree/main/skills/demo")
    assert tree is not None and tree.ref == "main" and tree.path == "skills/demo"
    raw = parse_github_source(
        "https://raw.githubusercontent.com/owner/repo/main/skills/demo/SKILL.md"
    )
    assert raw is not None and raw.path == "skills/demo/SKILL.md"
    assert parse_github_source("https://example.com/foo") is None
    assert parse_github_source("single-word") is None
    assert parse_github_source("not/valid/three/parts") is None


def test_install_remote_multi_skill_requires_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    collection = tmp_path / "collection"
    make_skill(collection, "first-skill")
    make_skill(collection, "second-skill", description="当需要第二个技能时使用；产出第二结果。")
    manager = SkillManager(make_settings(tmp_path))
    monkeypatch.setattr(manager, "_materialize", lambda source, tmp: (collection, True))

    with pytest.raises(SkillInstallError) as excinfo:
        manager.install("https://github.com/fake/repo")
    assert "指定" in str(excinfo.value)

    installed = manager.install("https://github.com/fake/repo", name="second-skill")
    assert len(installed) == 1
    assert installed[0].name == "second-skill"


def test_normalize_name_and_kebab() -> None:
    assert normalize_skill_name("My Skill!! v2") == "my-skill-v2"
    assert is_kebab_case("literature-survey")
    assert not is_kebab_case("Literature_Survey")
    with pytest.raises(SkillInstallError):
        normalize_skill_name("...")


# ---------------------------------------------------------------------------
# install_to（dsh / claude / project）
# ---------------------------------------------------------------------------


def test_install_to_dsh_custom_target_preserves_references(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = SkillManager(settings)
    dest = manager.install_to("literature-survey", "dsh")

    target_base = Path(settings.skills.targets["dsh"])
    assert dest == target_base / "literature-survey" and dest.is_dir()
    assert (dest / "SKILL.md").is_file()
    assert (dest / "references" / "search-strategies.md").is_file()
    assert manager.target_hint("dsh") in manager.last_install_messages[-1]
    assert "重启" in manager.target_hint("dsh")
    # 重复安装是幂等的（覆盖而非嵌套）
    again = manager.install_to("literature-survey", "dsh")
    assert again == dest
    assert (again / "literature-survey").exists() is False


def test_install_to_project_target(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = SkillManager(settings)
    dest = manager.install_to("training-debugging", "project")
    project_dir = settings.project_dir
    assert project_dir is not None
    assert dest == project_dir / ".sciforge" / "skills" / "training-debugging"
    assert (dest / "scripts" / "diagnose_nan.py").is_file()


def test_target_dir_resolution(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = SkillManager(settings)
    assert manager.target_dir(None) == manager.installed_dir
    assert manager.target_dir("dsh") == Path(settings.skills.targets["dsh"])
    assert manager.target_dir("~/custom-dir") == Path("~/custom-dir").expanduser()
    with pytest.raises(SkillTargetError):
        manager.target_dir("unknown-target-name")


def test_install_to_missing_skill_raises(tmp_path: Path) -> None:
    manager = SkillManager(make_settings(tmp_path))
    with pytest.raises(SkillNotFoundError):
        manager.install_to("no-such-skill", "dsh")


def test_install_all_to_dsh(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = SkillManager(settings)
    paths = manager.install_all_to("dsh")
    assert len(paths) == len(manager.list()) >= 16
    target = Path(settings.skills.targets["dsh"])
    for path in paths:
        assert path.is_dir()
        assert (path / "SKILL.md").is_file()
    assert target.is_dir()
    assert any("共安装" in message for message in manager.last_install_messages)


# ---------------------------------------------------------------------------
# 外部生态目录
# ---------------------------------------------------------------------------


def test_catalog_structure_and_lookup(tmp_path: Path) -> None:
    manager = SkillManager(make_settings(tmp_path))
    entries = manager.catalog()
    assert len(entries) >= 10
    names = {entry["name"] for entry in entries}
    for required in (
        "k-dense-scientific-agent-skills",
        "ai-research-skills",
        "awesome-scientific-skills",
        "anthropics-skills",
        "huggingface-skills",
        "daily-arxiv-ai-enhanced",
        "instsci",
    ):
        assert required in names
    for entry in entries:
        assert entry.get("name")
        assert entry.get("url", "").startswith("http")
        assert entry.get("description")
        assert entry.get("tags")
        assert entry.get("how_to_install")
        assert "license" in entry
        assert isinstance(entry.get("verified"), bool)
    assert manager.catalog_entry("k-dense") is not None
    assert manager.catalog_entry("instsci")["name"] == "instsci"
    assert manager.catalog_entry("no-such-catalog-entry") is None


def test_install_from_catalog_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_skill(tmp_path / "source", "demo-skill")
    manager = SkillManager(make_settings(tmp_path))
    monkeypatch.setattr(
        manager,
        "catalog",
        lambda: [{"name": "local-demo", "url": str(source), "description": "本地演示", "tags": ["experiment"]}],
    )
    installed = manager.install_from_catalog("local-demo", target="dsh")
    assert installed[0].name == "demo-skill"
    assert (installed[0] / "SKILL.md").is_file()
    assert any("local-demo" in message for message in manager.last_install_messages)
    with pytest.raises(SkillInstallError):
        manager.install_from_catalog("missing-entry")


# ---------------------------------------------------------------------------
# Agent 工具
# ---------------------------------------------------------------------------


def _tool_ctx(tmp_path: Path) -> ToolContext:
    settings = make_settings(tmp_path)
    return ToolContext(
        settings=settings,
        project_dir=tmp_path / "project",
        data_dir=tmp_path / "data",
        session_dir=tmp_path / "session",
    )


def test_skill_tools_registered_and_runnable(tmp_path: Path) -> None:
    assert get_tool("skill_list") is not None
    assert get_tool("skill_load") is not None
    assert get_tool("skill_search") is not None
    assert get_tool("skill_lint") is not None
    assert get_tool("skill_install") is not None

    ctx = _tool_ctx(tmp_path)
    listed = run_tool("skill_list", ctx)
    assert listed.ok
    assert listed.data["count"] >= 16

    found = run_tool("skill_search", ctx, {"query": "训练 OOM"})
    assert found.ok and found.data["count"] >= 1

    loaded = run_tool("skill_load", ctx, {"name": "literature-survey"})
    assert loaded.ok
    assert loaded.data["resources"]
    assert "工作流" in loaded.content

    linted = run_tool("skill_lint", ctx, {"name": "literature-survey"})
    assert linted.ok and linted.data["score"] >= 24

    missing = run_tool("skill_load", ctx, {"name": "no-such-skill"})
    assert missing.ok is False
    assert "未找到" in (missing.error or "")


def test_skill_install_tool_uses_isolated_target(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source", "demo-skill")
    ctx = _tool_ctx(tmp_path)
    result = run_tool("skill_install", ctx, {"source": str(source), "target": "dsh"})
    assert result.ok, result.error
    expected = Path(ctx.settings.skills.targets["dsh"]) / "demo-skill"
    assert expected.is_dir()
    assert str(expected) in result.data["installed"]
    assert "新开会话" in result.content or "重启" in result.content


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_app() -> typer.Typer:
    from sciforge.tools.skills import add_cli

    app = typer.Typer()
    add_cli(app)
    return app


def test_cli_list_search_show_lint(tmp_path: Path) -> None:
    runner = CliRunner()
    app = _cli_app()

    result = runner.invoke(app, ["skills", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["count"] >= 16

    result = runner.invoke(app, ["skills", "search", "配图", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["count"] >= 1

    result = runner.invoke(app, ["skills", "show", "literature-survey", "--content-only"])
    assert result.exit_code == 0, result.output
    assert "工作流" in result.output
    assert "name: literature-survey" not in result.output

    result = runner.invoke(app, ["skills", "lint", "literature-survey"])
    assert result.exit_code == 0, result.output
    assert "通过" in result.output

    result = runner.invoke(app, ["skills", "lint", "--all", "--json"])
    assert result.exit_code == 0, result.output
    reports = json.loads(result.output)
    assert len(reports) >= 16
    assert all(report["ok"] for report in reports)


def test_cli_install_dsh_with_isolated_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sciforge.tools import skills as skills_cli

    settings = make_settings(tmp_path)
    manager = SkillManager(settings)
    monkeypatch.setattr(skills_cli, "_manager_for_cli", lambda: manager)

    runner = CliRunner()
    result = runner.invoke(_cli_app(), ["skills", "install-dsh", "literature-survey"])
    assert result.exit_code == 0, result.output
    target = Path(settings.skills.targets["dsh"]) / "literature-survey"
    assert (target / "SKILL.md").is_file()
    assert "重启" in result.output or "新开会话" in result.output

    result = runner.invoke(_cli_app(), ["skills", "install-dsh", "--all", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["installed"]) >= 16


def test_cli_install_local_and_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sciforge.tools import skills as skills_cli

    source = make_skill(tmp_path / "source", "demo-skill")
    manager = SkillManager(make_settings(tmp_path))
    monkeypatch.setattr(skills_cli, "_manager_for_cli", lambda: manager)

    runner = CliRunner()
    result = runner.invoke(
        _cli_app(), ["skills", "install", str(source), "--target", "dsh", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert (Path(manager.settings.skills.targets["dsh"]) / "demo-skill" / "SKILL.md").is_file()

    result = runner.invoke(_cli_app(), ["skills", "catalog", "k-dense", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["count"] >= 1


def test_register_alias_equivalent() -> None:
    from sciforge.tools.skills import register

    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["skills", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["count"] >= 16
