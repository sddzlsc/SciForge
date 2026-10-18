"""科研 Skill 体系：发现 / 解析 / 搜索 / 安装 / lint（ARCHITECTURE §10、§14.2）。

Skill 目录结构（与 DSH / Claude Agent Skills 兼容）::

    skills_lib/<name>/SKILL.md           # 必有，YAML frontmatter + 正文
    skills_lib/<name>/references/*.md    # 可选：详细资料（渐进披露）
    skills_lib/<name>/scripts/*.py       # 可选：可复用脚本
    skills_lib/<name>/assets/*           # 可选：模板/图片

frontmatter 契约::

    ---
    name: literature-survey
    description: 何时使用（触发条件）+ 产出，1-2 句
    version: 0.1.0
    license: MIT
    compatibility: "sciforge>=0.1"
    metadata:
      author: SciForge
      tags: [literature, survey]
    ---

本模块只依赖标准库 + pyyaml + sciforge.config，不做任何网络请求（除非显式 install 远程源）。
"""

from __future__ import annotations

import contextlib
import importlib.resources as resources
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from sciforge.config import Settings, data_home, get_settings

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SKILL_FILENAME = "SKILL.md"
MAX_BODY_CHARS = 5000
MAX_DESCRIPTION_CHARS = 1024
MAX_SCORE = 30
PASS_SCORE = 24

# ARCHITECTURE §10 允许的 tags（额外 tag 只给 warning，不扣分）
CONTRACT_TAGS = (
    "literature",
    "reading",
    "ideation",
    "experiment",
    "remote",
    "writing",
    "figure",
    "review",
    "venue",
    "reproducibility",
    "meta",
)

# 安装目标默认目录（可被 settings.skills.targets 覆盖）
DEFAULT_TARGETS: dict[str, str] = {
    "dsh": "~/.dsh/skills",
    "claude": "~/.claude/skills",
    "codex": "~/.codex/skills",
}

TARGET_HINTS: dict[str, str] = {
    "dsh": "重启 DeepSeek Harness（或新开一个会话）后，DSH 即可自动发现该 skill。",
    "claude": "重启 Claude Code（或新开会话）后即可使用该 skill。",
    "codex": "重启 Codex（或新开会话）后即可使用该 skill。",
    "project": "该 skill 已进入当前科研项目，`sciforge skills list --source user` 可直接加载。",
}

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".DS_Store", ".pytest_cache", ".venv", "node_modules"
)

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_GITHUB_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/(?P<kind>tree|blob)/(?P<ref>[^/]+)(?:/(?P<path>.+?))?)?/?$"
)
_RAW_GITHUB_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/"
    r"(?P<ref>[^/]+)/(?P<path>.+)$"
)
_OWNER_REPO_RE = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$")


class SkillError(RuntimeError):
    """Skill 体系的可读错误基类。"""


class SkillNotFoundError(SkillError):
    """找不到指定 skill。"""


class SkillInstallError(SkillError):
    """安装源无法解析 / 复制失败。"""


class SkillTargetError(SkillError):
    """未知的安装目标。"""


# ---------------------------------------------------------------------------
# frontmatter 解析
# ---------------------------------------------------------------------------


@dataclass
class ParsedSkill:
    """一个 SKILL.md 的解析结果。"""

    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    raw: str = ""
    frontmatter_ok: bool = False
    error: str | None = None


def split_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    """把 Markdown 拆成 ``(frontmatter dict, 正文, 错误信息)``。

    容忍 BOM 与 CRLF；frontmatter 不是合法 YAML 时返回 ``({}, text, error)``。
    """
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip(), None  # 无 frontmatter 不算解析错误，由 lint 扣分
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            end = index
            break
    if end is None:
        return {}, text.strip(), "frontmatter 起始 '---' 后没有结束标记"
    raw_meta = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).strip()
    try:
        loaded = yaml.safe_load(raw_meta) if raw_meta.strip() else {}
    except yaml.YAMLError as exc:  # pragma: no cover - 防御性分支
        return {}, body, f"frontmatter YAML 解析失败: {exc}"
    if not isinstance(loaded, dict):
        return {}, body, "frontmatter 顶层必须是 YAML 映射（key: value）"
    meta = {str(key): value for key, value in loaded.items()}
    return meta, body, None


def parse_skill_md(path: Path) -> ParsedSkill:
    """读取并解析一个 SKILL.md（文件不存在时抛 :class:`SkillNotFoundError`）。"""
    path = Path(path)
    if not path.exists():
        raise SkillNotFoundError(f"SKILL.md 不存在: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError(f"SKILL.md 不是 UTF-8 编码: {path}") from exc
    meta, body, error = split_frontmatter(raw)
    return ParsedSkill(meta=meta, body=body, raw=raw, frontmatter_ok=error is None and bool(meta), error=error)


def is_kebab_case(name: str) -> bool:
    """目录/技能名是否为 kebab-case（DSH 与 Agent Skills 规范一致）。"""
    return bool(name) and bool(_KEBAB_RE.match(name))


def normalize_skill_name(raw: object) -> str:
    """把任意名字规整成安全的 kebab-case，用于落盘目录名（防路径穿越）。"""
    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise SkillInstallError(f"非法的 skill 名称: {raw!r}")
    return text[:80].strip("-")


def _meta_tags(meta: dict[str, Any]) -> list[str]:
    tags = meta.get("tags")
    if not tags:
        metadata = meta.get("metadata")
        if isinstance(metadata, dict):
            tags = metadata.get("tags")
    if isinstance(tags, str):
        tags = [part.strip() for part in re.split(r"[,，\s]+", tags) if part.strip()]
    if not isinstance(tags, (list, tuple)):
        return []
    return [str(item).strip() for item in tags if str(item).strip()]


def _meta_author(meta: dict[str, Any]) -> str:
    author = meta.get("author")
    if not author:
        metadata = meta.get("metadata")
        if isinstance(metadata, dict):
            author = metadata.get("author")
    return str(author or "")


# ---------------------------------------------------------------------------
# GitHub / 压缩包安装源解析
# ---------------------------------------------------------------------------


@dataclass
class GithubSource:
    """一个 GitHub 安装源（仓库根、子目录、或单个 SKILL.md）。"""

    owner: str
    repo: str
    ref: str = ""
    path: str = ""

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def web_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def parse_github_source(source: str) -> GithubSource | None:
    """解析 ``owner/repo``、GitHub 网页 URL、raw URL；不是 GitHub 源时返回 None。"""
    text = str(source).strip()
    match = _RAW_GITHUB_RE.match(text)
    if match:
        return GithubSource(
            owner=match.group("owner"),
            repo=match.group("repo"),
            ref=match.group("ref"),
            path=match.group("path"),
        )
    match = _GITHUB_RE.match(text)
    if match:
        return GithubSource(
            owner=match.group("owner"),
            repo=match.group("repo"),
            ref=match.group("ref") or "",
            path=(match.group("path") or "").strip("/"),
        )
    match = _OWNER_REPO_RE.match(text)
    if match:
        return GithubSource(owner=match.group("owner"), repo=match.group("repo"))
    return None


def _resource_dir(*parts: str) -> Path | None:
    """取包内资源目录的真实路径；非文件系统安装方式下退回 ``__file__``。"""
    fallback = Path(__file__).resolve().parent.joinpath(*parts)
    try:
        node = resources.files("sciforge")
        for part in parts:
            node = node.joinpath(part)
        if isinstance(node, Path):
            return node
    except Exception:  # noqa: BLE001 - 资源 API 在多后端下可能失败
        pass
    return fallback if fallback.exists() else None


def _iter_skill_dirs(base: Path) -> Iterator[Path]:
    """枚举一个 skills 根目录下的 skill 目录（兼容“集合目录”与“集合的集合”）。"""
    if not base or not base.is_dir():
        return
    if (base / SKILL_FILENAME).is_file():
        yield base
        return
    direct: list[Path] = []
    try:
        children = sorted(base.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return
    for child in children:
        if child.is_dir() and not child.name.startswith(".") and (child / SKILL_FILENAME).is_file():
            direct.append(child)
    if direct:
        yield from direct
        return
    # 退化场景：extra_dirs 指向某个生态仓库（如 Awesome-Scientific-Skills/skills/<repo>/...）
    seen: set[Path] = set()
    for candidate in sorted(base.rglob(SKILL_FILENAME), key=lambda path: str(path).lower()):
        skill_dir = candidate.parent
        rel_parts = skill_dir.relative_to(base).parts
        if len(rel_parts) > 3 or ".git" in rel_parts or skill_dir in seen:
            continue
        seen.add(skill_dir)
        yield skill_dir
        if len(seen) >= 500:  # 防御超大仓库
            return


# ---------------------------------------------------------------------------
# SkillMeta
# ---------------------------------------------------------------------------


@dataclass
class SkillMeta:
    """一个已发现 skill 的轻量元信息（§14.2 契约 + 少量便利字段）。"""

    name: str
    description: str
    path: Path
    tags: list[str] = field(default_factory=list)
    version: str = ""
    source: str = "bundled"  # bundled | installed | external | user
    dir: Path | None = None
    license: str = ""
    compatibility: str = ""
    author: str = ""

    @property
    def skill_dir(self) -> Path:
        return self.dir or self.path.parent

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "dir": str(self.skill_dir),
            "tags": list(self.tags),
            "version": self.version,
            "source": self.source,
            "license": self.license,
            "compatibility": self.compatibility,
            "author": self.author,
        }


# ---------------------------------------------------------------------------
# SkillManager
# ---------------------------------------------------------------------------


class SkillManager:
    """Skill 的发现、加载、搜索、安装与 lint。

    默认（``settings`` 为空）读取全局配置；未配置的路径按
    ``~/.sciforge/skills``（installed）与包内 ``sciforge/skills_lib``（bundled）处理。
    测试请把 ``SCIFORGE_HOME`` 指到 tmp_path，或传入带自定义 targets 的 Settings。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings: Settings = settings if settings is not None else get_settings()
        self.last_install_messages: list[str] = []

    # ---- 路径 ----
    @property
    def bundled_dir(self) -> Path | None:
        return _resource_dir("skills_lib")

    @property
    def installed_dir(self) -> Path:
        """已安装 skill 的默认目录：``~/.sciforge/skills``（受 SCIFORGE_HOME 控制）。"""
        return data_home() / "skills"

    @property
    def catalog_path(self) -> Path | None:
        return _resource_dir("catalog", "skills.json")

    @property
    def external_dirs(self) -> list[Path]:
        dirs = getattr(getattr(self.settings, "skills", None), "extra_dirs", None) or []
        result: list[Path] = []
        for raw in dirs:
            try:
                path = Path(str(raw)).expanduser()
            except (TypeError, ValueError):
                continue
            if path not in result:
                result.append(path)
        return result

    @property
    def user_dir(self) -> Path | None:
        """项目级 skill 目录：``<project>/.sciforge/skills``（source="user"）。"""
        project = getattr(self.settings, "project_dir", None)
        if project is None:
            return None
        return Path(str(project)).expanduser() / ".sciforge" / "skills"

    def _sources(self) -> list[tuple[Path | None, str]]:
        """按优先级返回 (目录, source) —— 先出现者覆盖后出现者。"""
        return [
            (self.user_dir, "user"),
            (self.installed_dir, "installed"),
            *[(path, "external") for path in self.external_dirs],
            (self.bundled_dir, "bundled"),
        ]

    # ---- 发现与读取 ----
    def _scan(self) -> dict[str, SkillMeta]:
        found: dict[str, SkillMeta] = {}
        for base, source in self._sources():
            if base is None:
                continue
            for skill_dir in _iter_skill_dirs(Path(base)):
                meta = self._meta_from_dir(skill_dir, source)
                if meta is None:
                    continue
                found.setdefault(meta.name, meta)
                # 允许用目录名兜底找到 frontmatter name 不规范的外部 skill
                found.setdefault(skill_dir.name, meta)
        return found

    def _meta_from_dir(self, skill_dir: Path, source: str) -> SkillMeta | None:
        skill_md = skill_dir / SKILL_FILENAME
        if not skill_md.is_file():
            return None
        try:
            parsed = parse_skill_md(skill_md)
        except SkillError:
            return None
        meta = parsed.meta
        front_name = str(meta.get("name") or "").strip()
        dir_name = skill_dir.name
        if front_name and is_kebab_case(front_name):
            name = front_name
        elif is_kebab_case(dir_name):
            name = dir_name
        else:
            name = front_name or dir_name
        description = str(meta.get("description") or "").strip()
        return SkillMeta(
            name=name,
            description=description,
            path=skill_md,
            tags=_meta_tags(meta),
            version=str(meta.get("version") or ""),
            source=source,
            dir=skill_dir,
            license=str(meta.get("license") or ""),
            compatibility=str(meta.get("compatibility") or ""),
            author=_meta_author(meta),
        )

    def list(self, *, source: str | None = None, tag: str | None = None) -> list[SkillMeta]:
        """列出所有 skill（按 name 排序；可按 source / tag 过滤）。"""
        skills = sorted(self._scan().values(), key=lambda item: (item.name.lower(), item.source))
        if source:
            skills = [skill for skill in skills if skill.source == source]
        if tag:
            skills = [skill for skill in skills if tag in skill.tags]
        return skills

    def get(self, name: str) -> SkillMeta | None:
        """按名字取 skill；找不到返回 None。"""
        key = str(name or "").strip()
        if not key:
            return None
        found = self._scan()
        if key in found:
            return found[key]
        lowered = key.lower()
        for skill_name, meta in found.items():
            if skill_name.lower() == lowered:
                return meta
        return None

    def load(self, name: str) -> str:
        """返回 SKILL.md 完整文本（含 frontmatter，供 agent 按需加载）。"""
        meta = self.get(name)
        if meta is None:
            raise SkillNotFoundError(f"未找到 skill: {name}（用 `sciforge skills list` 查看可用技能）")
        return meta.path.read_text(encoding="utf-8")

    def read_resource(self, name: str, relpath: str) -> str:
        """读取 skill 内的 references/scripts 文件（拒绝目录穿越）。"""
        meta = self.get(name)
        if meta is None:
            raise SkillNotFoundError(f"未找到 skill: {name}")
        target = (meta.skill_dir / str(relpath)).resolve()
        root = meta.skill_dir.resolve()
        if root != target and root not in target.parents:
            raise SkillError(f"拒绝读取 skill 目录之外的文件: {relpath}")
        if not target.is_file():
            raise SkillNotFoundError(f"skill {name} 中不存在资源: {relpath}")
        return target.read_text(encoding="utf-8")

    def resources(self, name: str) -> list[str]:
        """列出某个 skill 的 references/scripts/assets 相对路径。"""
        meta = self.get(name)
        if meta is None:
            raise SkillNotFoundError(f"未找到 skill: {name}")
        out: list[str] = []
        for sub in ("references", "scripts", "assets"):
            base = meta.skill_dir / sub
            if base.is_dir():
                out.extend(sorted(str(path.relative_to(meta.skill_dir)) for path in base.rglob("*") if path.is_file()))
        return out

    # ---- 搜索 ----
    def search(self, query: str, *, limit: int = 10) -> list[SkillMeta]:
        """按名字/描述/tags/正文做关键词检索（轻量、离线、可解释）。"""
        terms = [term for term in re.split(r"[\s,，、;；/]+", str(query or "").strip()) if term]
        if not terms:
            return []
        scored: list[tuple[int, SkillMeta]] = []
        for skill in self.list():
            score = self._score_skill(skill, terms)
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [skill for _, skill in scored[: max(0, int(limit))]]

    @staticmethod
    def _score_skill(skill: SkillMeta, terms: list[str]) -> int:
        name = skill.name.lower()
        description = skill.description.lower()
        tags = [tag.lower() for tag in skill.tags]
        try:
            body = skill.path.read_text(encoding="utf-8").lower()
        except OSError:
            body = ""
        score = 0
        for term in terms:
            needle = term.lower()
            if needle == name:
                score += 120
            elif needle in name:
                score += 50
            if any(needle == tag for tag in tags):
                score += 45
            elif any(needle in tag for tag in tags):
                score += 25
            if needle in description:
                score += 18
            hits = body.count(needle)
            if hits:
                score += min(12, 2 + hits)
        return score

    # ---- 安装目标 ----
    def target_dir(self, target: str | None) -> Path:
        """把 target 解析为落盘目录；``target=None`` 表示已安装目录。"""
        if target is None:
            return self.installed_dir
        raw = str(target).strip()
        if not raw:
            return self.installed_dir
        if raw == "project":
            project = getattr(self.settings, "project_dir", None) or Path.cwd()
            return Path(str(project)).expanduser() / ".sciforge" / "skills"
        targets = dict(DEFAULT_TARGETS)
        configured = getattr(getattr(self.settings, "skills", None), "targets", None) or {}
        if isinstance(configured, dict):
            targets.update({str(key): str(value) for key, value in configured.items()})
        if raw in targets:
            return Path(targets[raw]).expanduser()
        if raw.startswith(("~", ".", "/")):
            return Path(raw).expanduser()
        known = "、".join(sorted({*DEFAULT_TARGETS, *targets, "project"}))
        raise SkillTargetError(f"未知安装目标 {raw!r}；可选: {known}，或直接给一个路径（如 ~/.dsh/skills）。")

    @staticmethod
    def target_hint(target: str | None) -> str:
        key = str(target or "dsh")
        if key in TARGET_HINTS:
            return TARGET_HINTS[key]
        if key == "project":
            return TARGET_HINTS["project"]
        return f"已复制到 {key}；重启对应 agent（或新开会话）后即可发现该 skill。"

    # ---- 安装 ----
    def install(
        self,
        source: str,
        *,
        target: str | None = None,
        name: str | None = None,
        ref: str | None = None,
    ) -> list[Path]:
        """安装 skill。

        ``source`` 支持：

        * 本地 skill 目录（含 SKILL.md）或包含多个 skill 的目录；
        * 本地 ``.zip`` / ``.tar.gz`` 压缩包；
        * GitHub 仓库 URL / ``owner/repo`` 简写（浅克隆后取子目录）；
        * GitHub ``tree`` / ``blob`` 子路径 URL、raw.githubusercontent.com 单个 SKILL.md URL。

        ``target`` 为空时安装到 ``~/.sciforge/skills``；否则按
        ``settings.skills.targets`` 或内置 dsh/claude/codex 映射解析。
        返回被写入的 skill 目录列表。
        """
        dest_base = self.target_dir(target)
        dest_base.mkdir(parents=True, exist_ok=True)
        self.last_install_messages = []
        with tempfile.TemporaryDirectory(prefix="sciforge-skill-") as tmp:
            root, is_remote = self._materialize(source, Path(tmp))
            skill_dirs = list(_iter_skill_dirs(root))
            if not skill_dirs:
                raise SkillInstallError(
                    f"安装源中没有找到 {SKILL_FILENAME}: {source}\n"
                    "提示：可以给仓库内子目录 URL（.../tree/main/skills/xxx）或直接给 SKILL.md 的 raw URL。"
                )
            if name:
                wanted = normalize_skill_name(name)
                matched = [path for path in skill_dirs if self._candidate_names(path) & {wanted, name}]
                if not matched:
                    available = "、".join(path.name for path in skill_dirs[:20])
                    raise SkillInstallError(f"安装源中没有名为 {name!r} 的 skill。候选: {available}")
                skill_dirs = matched
            elif is_remote and len(skill_dirs) > 1:
                available = "、".join(path.name for path in skill_dirs[:20])
                raise SkillInstallError(
                    f"仓库包含 {len(skill_dirs)} 个 skill，请用 name 参数指定其中一个。候选: {available}\n"
                    f"示例: sciforge skills install {source} --name <候选名>"
                )
            installed: list[Path] = []
            for skill_dir in skill_dirs:
                dest_name = self._dest_name(skill_dir, name)
                dest = dest_base / dest_name
                if self._same_dir(skill_dir, dest):
                    installed.append(dest)
                    continue
                if dest.exists() or dest.is_symlink():
                    if dest.is_dir() and not dest.is_symlink():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.copytree(skill_dir, dest, ignore=_COPY_IGNORE)
                self._ensure_skill_md(dest, dest_name)
                installed.append(dest)
                self.last_install_messages.append(f"已安装 {dest_name} → {dest}")
        return installed

    def install_from_catalog(
        self,
        catalog_name: str,
        *,
        target: str | None = None,
        skill: str | None = None,
        ref: str | None = None,
    ) -> list[Path]:
        """从 ``sciforge/catalog/skills.json`` 的生态目录安装一个条目。"""
        entry = self.catalog_entry(catalog_name)
        if entry is None:
            raise SkillInstallError(f"外部生态目录中没有 {catalog_name!r}，用 `sciforge skills catalog` 查看全部条目。")
        url = str(entry.get("url") or entry.get("source") or "").strip()
        if not url:
            raise SkillInstallError(f"目录条目 {catalog_name!r} 缺少 url 字段")
        selector = skill or entry.get("skill")
        selector = str(selector) if selector else None
        if selector:
            selector = normalize_skill_name(selector)
        installed = self.install(url, target=target, name=selector, ref=ref or entry.get("ref") or None)
        self.last_install_messages.insert(0, f"来源: {catalog_name} ({url})")
        return installed

    def _materialize(self, source: str, tmp: Path) -> tuple[Path, bool]:
        """把安装源落地到本地目录，返回 (含 skill 的根目录, 是否远程)。"""
        text = str(source).strip().strip('"').strip("'")
        if not text:
            raise SkillInstallError("安装源为空")
        local = Path(text).expanduser()
        if local.exists():
            if local.is_dir():
                return local, False
            if local.suffix.lower() in {".zip"}:
                return self._extract_zip(local, tmp), False
            if local.suffix.lower() in {".tar", ".gz", ".tgz"}:
                return self._extract_tar(local, tmp), False
            if local.name == SKILL_FILENAME:
                return local.parent, False
            raise SkillInstallError(f"本地安装源必须是 skill 目录、压缩包或 SKILL.md: {local}")
        github = parse_github_source(text)
        if github is None:
            raise SkillInstallError(
                f"无法识别的安装源: {source}\n"
                "支持：本地 skill 目录 / .zip/.tar.gz / GitHub URL / owner/repo / raw.githubusercontent.com 的 SKILL.md URL。"
            )
        if not shutil.which("git"):
            raise SkillInstallError("需要 git 才能从 GitHub 安装；请先安装 git，或直接下载 SKILL.md 用本地目录安装。")
        clone_dir = tmp / "repo"
        command = ["git", "clone", "--depth", "1", "--single-branch"]
        ref = github.ref
        if ref:
            command += ["--branch", ref]
        command += [github.clone_url, str(clone_dir)]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SkillInstallError("git clone 超时（180s）；请检查网络/代理后重试。") from exc
        except OSError as exc:
            raise SkillInstallError(f"无法执行 git: {exc}") from exc
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip().splitlines()[-3:]
            raise SkillInstallError(
                "git clone 失败：" + " / ".join(stderr) + "\n提示：网络不通时可在浏览器下载仓库 zip 后本地安装。"
            )
        root = clone_dir
        if github.path:
            candidate = (clone_dir / github.path).resolve()
            if clone_dir.resolve() not in candidate.parents and candidate != clone_dir.resolve():
                raise SkillInstallError(f"子路径越界: {github.path}")
            if candidate.is_file() and candidate.name == SKILL_FILENAME:
                root = candidate.parent
            elif candidate.is_dir():
                root = candidate
            else:
                raise SkillInstallError(f"仓库中不存在子路径: {github.path}")
        return root, True

    @staticmethod
    def _extract_zip(archive: Path, tmp: Path) -> Path:
        target = tmp / "unzipped"
        target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    dest = (target / member.filename).resolve()
                    if target.resolve() not in dest.parents and dest != target.resolve():
                        raise SkillInstallError(f"压缩包包含非法路径: {member.filename}")
                bundle.extractall(target)
        except zipfile.BadZipFile as exc:
            raise SkillInstallError(f"不是合法的 zip 文件: {archive}") from exc
        return target

    @staticmethod
    def _extract_tar(archive: Path, tmp: Path) -> Path:
        target = tmp / "untarred"
        target.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(archive) as bundle:
                if hasattr(tarfile, "data_filter"):
                    bundle.extractall(target, filter="data")
                else:  # pragma: no cover - Python 3.10/3.11 早期版本
                    bundle.extractall(target)
        except (tarfile.TarError, OSError) as exc:
            raise SkillInstallError(f"不是合法的 tar 包: {archive}") from exc
        return target

    @staticmethod
    def _candidate_names(skill_dir: Path) -> set[str]:
        names = {skill_dir.name}
        try:
            parsed = parse_skill_md(skill_dir / SKILL_FILENAME)
            front = str(parsed.meta.get("name") or "").strip()
            if front:
                names.add(front)
        except SkillError:
            pass
        return names

    def _dest_name(self, skill_dir: Path, name: str | None) -> str:
        if name:
            return normalize_skill_name(name)
        parsed: ParsedSkill | None = None
        try:
            parsed = parse_skill_md(skill_dir / SKILL_FILENAME)
        except SkillError:
            parsed = None
        front = str(parsed.meta.get("name") or "").strip() if parsed else ""
        for candidate in (front, skill_dir.name):
            if candidate and is_kebab_case(candidate):
                return candidate
        if front:
            return normalize_skill_name(front)
        return normalize_skill_name(skill_dir.name)

    @staticmethod
    def _ensure_skill_md(skill_dir: Path, dest_name: str) -> None:
        """兜底：确保拷贝后的目录里有 SKILL.md（外部源文件名大小写不规范时修正）。"""
        if (skill_dir / SKILL_FILENAME).is_file():
            return
        for candidate in skill_dir.iterdir():
            if candidate.is_file() and candidate.name.lower() == SKILL_FILENAME.lower():
                candidate.rename(skill_dir / SKILL_FILENAME)
                return
        raise SkillInstallError(f"安装后的目录缺少 {SKILL_FILENAME}: {skill_dir}（目标名 {dest_name}）")

    @staticmethod
    def _same_dir(left: Path, right: Path) -> bool:
        with contextlib.suppress(OSError):
            return left.resolve() == right.resolve()
        return False

    # ---- DSH / Claude / project 集成 ----
    def install_to(self, name: str, target: str) -> Path:
        """把已发现的 skill 目录复制到 target；返回目标 skill 目录。

        典型用法：``manager.install_to("literature-survey", "dsh")``
        → 复制到 ``~/.dsh/skills/literature-survey/``（保留 references/scripts），
        重启 / 新开会话后 DSH 即可使用。
        """
        meta = self.get(name)
        if meta is None:
            suggestions = [skill.name for skill in self.search(name, limit=5)]
            hint = f"；相近技能: {', '.join(suggestions)}" if suggestions else ""
            raise SkillNotFoundError(f"未找到 skill: {name}（先 `sciforge skills list`）{hint}")
        dest_base = self.target_dir(target)
        dest_base.mkdir(parents=True, exist_ok=True)
        dest = dest_base / meta.name
        if self._same_dir(meta.skill_dir, dest):
            return dest
        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        shutil.copytree(meta.skill_dir, dest, ignore=_COPY_IGNORE)
        self._ensure_skill_md(dest, meta.name)
        self.last_install_messages.append(
            f"已安装 {meta.name} ({meta.source}) → {dest}；{self.target_hint(target)}"
        )
        return dest

    def install_all_to(self, target: str, *, source: str | None = None) -> list[Path]:
        """把全部（或指定来源的）skill 安装到 target；返回目标目录列表。"""
        skills = self.list(source=source)
        if not skills:
            raise SkillNotFoundError("没有可安装的 skill")
        paths = [self.install_to(skill.name, target) for skill in skills]
        self.last_install_messages.insert(0, f"共安装 {len(paths)} 个 skill 到 {self.target_dir(target)}；{self.target_hint(target)}")
        return paths

    # ---- lint（skill-metric 规则，0-30 分）----
    def lint(self, name: str) -> dict[str, Any]:
        """按 skill-metric 风格静态评分。

        返回 ``{"ok": bool, "score": int, "issues": [...], "checks": [...], ...}``，
        满分 30（格式 10 / 完整性 10 / 写作结构 10），>=24 且无 critical 视为通过。
        """
        meta = self.get(name)
        if meta is None:
            raise SkillNotFoundError(f"未找到 skill: {name}")
        return self.lint_dir(meta.skill_dir)

    def lint_dir(self, skill_dir: str | Path) -> dict[str, Any]:
        """对任意 skill 目录做 lint（测试 / 第三方 skill 体检）。"""
        skill_dir = Path(skill_dir).expanduser()
        skill_md = skill_dir / SKILL_FILENAME
        checks: list[dict[str, Any]] = []
        issues: list[str] = []
        critical = False

        def add(check: str, points: int, ok: bool, issue: str = "") -> None:
            nonlocal critical
            checks.append({"check": check, "points": points, "ok": bool(ok)})
            if not ok and issue:
                severity = "critical" if check in _CRITICAL_CHECKS else "warning"
                if severity == "critical":
                    critical = True
                issues.append(f"[{severity}] {issue}（-{points} 分）")

        # ---- 格式 10 分 ----
        add("SKILL.md 存在", 2, skill_md.is_file(), f"缺少 {SKILL_FILENAME}（skill 目录: {skill_dir}）")
        add("目录名为 kebab-case", 1, is_kebab_case(skill_dir.name), f"目录名 {skill_dir.name!r} 不是 kebab-case")
        readme = skill_dir / "README.md"
        add("目录内不放 README.md", 1, not readme.exists(), "skill 目录内不应有 README.md（内容应写进 SKILL.md 或 references/）")
        parsed = ParsedSkill()
        if skill_md.is_file():
            try:
                parsed = parse_skill_md(skill_md)
            except SkillError as exc:
                issues.append(f"[critical] {exc}（-0 分）")
                critical = True
        add("frontmatter 可解析", 2, parsed.frontmatter_ok, parsed.error or "缺少 YAML frontmatter（--- 包裹的 name/description 等）")
        front_name = str(parsed.meta.get("name") or "").strip()
        add("name 与目录名一致", 2, bool(front_name) and front_name == skill_dir.name, f"frontmatter name={front_name!r} 与目录名 {skill_dir.name!r} 不一致")
        description = str(parsed.meta.get("description") or "").strip()
        add("description 非空", 1, bool(description), "缺少 description（应说明何时使用与产出）")
        add(
            "description 简短无 XML",
            1,
            0 < len(description) <= MAX_DESCRIPTION_CHARS and "<" not in description,
            f"description 需 <= {MAX_DESCRIPTION_CHARS} 字符且不含 XML 标签",
        )

        # ---- 完整性 10 分 ----
        tags = _meta_tags(parsed.meta)
        supplementary = [
            sub for sub in ("references", "scripts", "assets") if (skill_dir / sub).is_dir() and any((skill_dir / sub).iterdir())
        ]
        body = parsed.body
        add("license 字段", 1, bool(str(parsed.meta.get("license") or "").strip()), "缺少 license 字段（如 MIT）")
        add(
            "compatibility 字段",
            1,
            bool(str(parsed.meta.get("compatibility") or "").strip()),
            "缺少 compatibility 字段（如 \"sciforge>=0.1\"）",
        )
        metadata = parsed.meta.get("metadata")
        add(
            "metadata 作者/标签",
            1,
            isinstance(metadata, dict) and bool(metadata.get("author") or metadata.get("tags")),
            "缺少 metadata.author / metadata.tags",
        )
        add("tags 非空", 1, bool(tags), "tags 为空（建议使用 literature/reading/ideation/experiment/remote/writing/figure/review/venue/reproducibility/meta）")
        has_example = "```" in body or bool(re.search(r"(示例|例子|example|sample|例如)", body, re.I))
        add("含具体示例", 2, has_example, "正文缺少可执行的示例（命令、输入输出样例或 spec 片段）")
        has_failure = bool(
            re.search(r"(失败|错误|异常|排错|排查|降级|回滚|报错|OOM|NaN|fail|error|troubleshoot|fallback|rollback)", body, re.I)
        )
        add("含失败处理", 2, has_failure, "正文缺少失败处理 / 排错说明（如 OOM、loss NaN、网络失败、引用幻觉）")
        add(
            "含 references/scripts/assets",
            1,
            bool(supplementary),
            "复杂 skill 建议提供 references/ 或 scripts/（渐进披露）",
        )
        referenced = any(f"{sub}/" in body for sub in supplementary)
        add("正文引用扩展资源", 1, referenced, "正文未引用 references/ 或 scripts/ 中的文件")

        # ---- 写作结构 10 分 ----
        body_len = len(body)
        add(
            "正文 < 5k 字符",
            3,
            body_len < MAX_BODY_CHARS,
            f"正文 {body_len} 字符，超过 {MAX_BODY_CHARS} 上限；请把细节移入 references/",
        )
        trigger = bool(
            re.search(
                r"(当|何时|使用|适用|适合|用于|触发|场景|use\s+when|when\s+to\s+use|when\s+the\s+user|trigger)",
                description,
                re.I,
            )
        )
        add("description 含触发条件", 2, trigger, "description 未写清“何时使用”（触发条件）")
        workflow = bool(re.search(r"(工作流|步骤|流程|执行步骤|workflow|##\s*步骤|step\s*\d)", body, re.I))
        add("含工作流步骤", 2, workflow, "正文缺少工作流 / 步骤")
        tool_map = bool(
            re.search(
                r"(sciforge\s+[a-z][\w-]*|lit_search|paper_read|paper_cite|remote_run|gpu_status|exp_launch|"
                r"figure_architecture|figure_plot|figure_table|paper_section|paper_compile|paper_review|skill_load|"
                r"skill_search|corresponding tools?|对应工具)",
                body,
                re.I,
            )
        )
        add("含 SciForge 工具对应", 2, tool_map, "未说明与 SciForge 工具/CLI 的对应关系")
        checklist = bool(re.search(r"(检查清单|质量检查|自查|验收|checklist|quality\s+check)", body, re.I))
        add("含质量检查清单", 1, checklist, "正文缺少质量检查清单（checklist）")

        score = min(MAX_SCORE, sum(int(check["points"]) for check in checks if check["ok"]))
        # 额外提示：未在契约 tags 内的 tag（不扣分）
        unknown = [tag for tag in tags if tag not in CONTRACT_TAGS]
        if unknown:
            issues.append(f"[info] tags {unknown} 不在 ARCHITECTURE §10 契约列表内（不扣分，便于自定义分类）")
        return {
            "ok": score >= PASS_SCORE and not critical,
            "score": score,
            "max_score": MAX_SCORE,
            "pass_score": PASS_SCORE,
            "name": skill_dir.name,
            "path": str(skill_md),
            "body_chars": body_len,
            "issues": issues,
            "checks": checks,
        }

    # ---- 外部生态目录 ----
    def catalog(self) -> list[dict[str, Any]]:
        """读取包内 ``catalog/skills.json`` 外部生态目录。"""
        path = self.catalog_path
        if path is None or not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries = data.get("skills") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict)]

    def catalog_entry(self, name: str) -> dict[str, Any] | None:
        key = str(name or "").strip().lower()
        if not key:
            return None
        exact: dict[str, Any] | None = None
        partial: dict[str, Any] | None = None
        for entry in self.catalog():
            entry_name = str(entry.get("name") or "").lower()
            if not entry_name:
                continue
            if entry_name == key:
                exact = entry
                break
            aliases = [str(alias).lower() for alias in entry.get("aliases", []) or []]
            if key in aliases and partial is None:
                partial = entry
            elif partial is None and (key in entry_name or entry_name in key):
                partial = entry
        return exact or partial


_CRITICAL_CHECKS = {
    "SKILL.md 存在",
    "frontmatter 可解析",
    "name 与目录名一致",
}


__all__ = [
    "SKILL_FILENAME",
    "MAX_BODY_CHARS",
    "MAX_SCORE",
    "PASS_SCORE",
    "CONTRACT_TAGS",
    "DEFAULT_TARGETS",
    "TARGET_HINTS",
    "SkillError",
    "SkillNotFoundError",
    "SkillInstallError",
    "SkillTargetError",
    "SkillMeta",
    "ParsedSkill",
    "GithubSource",
    "split_frontmatter",
    "parse_skill_md",
    "is_kebab_case",
    "normalize_skill_name",
    "parse_github_source",
    "SkillManager",
]
