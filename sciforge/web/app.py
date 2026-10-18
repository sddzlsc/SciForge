"""SciForge 本地 Web 控制台后端（FastAPI）。

设计原则：

- **零外部构建**：静态页面是手写 HTML/CSS/JS，离线可用，不依赖 CDN。
- **模块缺失不崩溃**：对 ``sciforge.library`` / ``skills_manager`` / ``pipeline.engine``
  等模块全部延迟导入；未就绪时端点返回 ``{"available": false, ...}`` 的友好 JSON。
- **安全**：默认只监听 127.0.0.1；``/files`` / ``/figures`` 只暴露项目根内文件；
  ``/api/servers`` 永不返回任何密钥（api key / 密码 / 环境变量值）。
- **事件复用**：``POST /api/chat`` 把 AgentLoop 的 EventBus 事件转成 SSE；
  ``POST /api/pipeline/run`` 在后台线程跑 PipelineRunner，前端轮询状态。

端点清单见 :func:`create_app` 与 ``docs/ARCHITECTURE.md`` §14.4。
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import os
import queue
import re
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sciforge.config import (
    ProviderConfig,
    RoleConfig,
    ServerConfig,
    Settings,
    ensure_dirs,
    find_project_root,
    get_settings,
    global_config_path,
    save_global_config,
)
from sciforge.core.events import Event, EventBus
from sciforge.core.types import ChatMessage, LLMResponse, ToolCall, Usage

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
REPO_ROOT = WEB_DIR.parents[1]
FIXTURES_DIR = REPO_ROOT / "examples" / "demo" / "fixtures"

#: 单次对话最长等待时间（秒）。超时后 SSE 主动结束，避免连接挂死。
CHAT_TIMEOUT_SECONDS = float(os.environ.get("SCIFORGE_WEB_CHAT_TIMEOUT", "300"))
#: GPU 探测（SSH 执行 nvidia-smi）最长等待时间（秒）。
GPU_PROBE_TIMEOUT = float(os.environ.get("SCIFORGE_WEB_GPU_TIMEOUT", "15"))
#: SSE 心跳间隔（秒），防止反向代理断开空闲连接。
SSE_HEARTBEAT_SECONDS = 15.0

_GALLERY_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_DOWNLOAD_EXTS = _GALLERY_EXTS | {".pdf"}
_ARTIFACT_EXTS = _DOWNLOAD_EXTS | {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".bib", ".tex", ".py"}
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'"
)


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = ""
    mock: bool | None = None
    session_id: str | None = None
    max_steps: int | None = Field(default=None, ge=1, le=100)
    # 仅 mock 模式生效：用脚本 router 让 Agent 真实执行一次只读工具，
    # 以便无 API key 时演示「工具调用折叠卡片」；不伪造事件，工具确实被执行。
    demo_tools: bool = False


class PipelineRunRequest(BaseModel):
    recipe: str = "full"
    topic: str = ""
    mock: bool | None = None
    until: str | None = None


class SkillInstallRequest(BaseModel):
    name: str
    target: str = "dsh"


class ProviderForm(BaseModel):
    """GUI 里「模型 Provider」表单（密钥只写本机配置文件，不随响应返回）。"""

    name: str
    base_url: str
    default_model: str
    type: str = "openai-compatible"
    models: list[str] | str = Field(default_factory=list)
    api_key: str | None = None
    clear_api_key: bool = False
    make_default: bool = False
    api_key_env: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=2_000_000)
    supports_tools: bool = True
    input_cost_per_mtok: float | None = Field(default=None, ge=0)
    output_cost_per_mtok: float | None = Field(default=None, ge=0)


class RoleEntry(BaseModel):
    provider: str | None = None
    model: str | None = None


class RolesForm(BaseModel):
    roles: dict[str, RoleEntry]


class LlmTestRequest(BaseModel):
    provider: str | None = None
    role: str | None = None
    model: str | None = None
    prompt: str = "用一句话说明你是谁。"


class LiteratureForm(BaseModel):
    """GUI「文献检索设置」表单：可选 S2 Key / 邮箱 / 每日 arXiv 语言。"""

    email: str | None = None
    unpaywall_email: str | None = None
    s2_api_key: str | None = None
    clear_s2_api_key: bool = False
    s2_api_key_env: str | None = None
    daily_arxiv_language: str = "Chinese"


class ServerForm(BaseModel):
    """GUI 里「GPU 服务器」表单；不包含任何密码字段。"""

    name: str
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    user: str = "root"
    identity_file: str | None = None
    proxy_jump: str | None = None
    workdir: str = "~/sciforge"
    conda_env: str | None = None
    python: str = "python"
    tmux_session: str = "sciforge"
    scheduler: str = "none"
    partition: str | None = None
    max_parallel_jobs: int = Field(default=2, ge=1, le=64)
    auto_mkdir: bool = True


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _version() -> str:
    try:
        import sciforge

        return str(sciforge.__version__)
    except Exception:  # noqa: BLE001
        return "0.0.0"


def _now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts if ts is not None else time.time()))


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _jsonable(value: Any, *, max_str: int = 12000, _depth: int = 0) -> Any:
    """把任意对象递归转成可 JSON 序列化的结构（截断超长字符串）。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max_str] + "…（已截断）"
    if _depth > 8:
        return repr(value)[:500]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v, max_str=max_str, _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, max_str=max_str, _depth=_depth + 1) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _jsonable(getattr(value, f.name, None), max_str=max_str, _depth=_depth + 1)
            for f in dataclasses.fields(value)
        }
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"), max_str=max_str, _depth=_depth + 1)
        except Exception:  # noqa: BLE001
            pass
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _jsonable(to_dict(), max_str=max_str, _depth=_depth + 1)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "__dict__"):
        return {
            k: _jsonable(v, max_str=max_str, _depth=_depth + 1)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return str(value)[:max_str]


def _sse(data: dict[str, Any], event: str | None = None) -> str:
    """构造一条 SSE 消息。data 必须是单行 JSON（不能包含裸换行）。"""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    if event:
        return f"event: {event}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def _resolve_project_dir(settings: Settings) -> Path:
    raw: Any = settings.project_dir or find_project_root() or Path.cwd()
    return Path(str(raw)).expanduser().resolve()


def _safe_join(base: Path, rel: str) -> Path:
    """把 URL 相对路径安全地解析到 base 内；越界/不存在一律 404（不泄露路径信息）。"""
    raw = (rel or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith("/") or raw.startswith("~"):
        raise HTTPException(status_code=404, detail="文件不存在")
    target = (base / raw).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在") from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


def _rel_posix(base: Path, path: str | Path) -> str | None:
    """把绝对路径转成相对项目根的正斜杠路径；不在项目内返回 None。"""
    try:
        target = Path(path).expanduser().resolve()
        return target.relative_to(base.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _clean_session_id(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    if not _SESSION_ID_RE.match(value) or ".." in value:
        raise HTTPException(status_code=400, detail="session_id 非法（只允许字母/数字/._-，且不含 ..）")
    return value


def _new_session_id() -> str:
    return f"web-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _is_mock_env() -> bool:
    return os.environ.get("SCIFORGE_MOCK", "").lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# 各模块的延迟探针
# ---------------------------------------------------------------------------


def _bootstrap_tools() -> None:
    """import 工具包以触发各工具自注册；模块缺失时只记录 import_errors。"""
    try:
        importlib.import_module("sciforge.tools")
    except Exception:  # noqa: BLE001 - 工具包损坏不应阻止 Web 启动
        return


def _tool_health() -> dict[str, Any]:
    try:
        import sciforge.tools as tools_pkg

        return dict(tools_pkg.tool_health())
    except Exception as exc:  # noqa: BLE001
        try:
            from sciforge.core.registry import list_tools

            names = [tool.name for tool in list_tools()]
            return {"count": len(names), "names": names, "by_tag": {}, "import_errors": {"tools": str(exc)}}
        except Exception:  # noqa: BLE001
            return {"count": 0, "names": [], "by_tag": {}, "import_errors": {"tools": str(exc)}}


def _tool_entries() -> list[dict[str, Any]]:
    try:
        from sciforge.core.registry import list_tools

        entries = []
        for tool in list_tools():
            entries.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "tags": list(getattr(tool, "tags", ()) or ()),
                    "requires": list(getattr(tool, "requires", ()) or ()),
                    "parameters": getattr(tool, "parameters", {}),
                }
            )
        return entries
    except Exception:  # noqa: BLE001
        return []


def _llm_health(settings: Settings) -> dict[str, Any]:
    try:
        from sciforge.llm.router import LLMRouter

        router = LLMRouter(settings)
        info = dict(router.check("executor"))
        info["force_mock"] = bool(router.force_mock)
        try:
            info["provider_name"] = settings.llm.provider_name_for("executor")
        except Exception:  # noqa: BLE001
            info["provider_name"] = settings.llm.default
        return info
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "provider": getattr(settings.llm, "default", ""),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _auto_mock(settings: Settings, requested: bool | None) -> bool:
    """mock 缺省时：显式环境变量优先；否则检测不到 API key 就自动离线 Mock。"""
    if requested is not None:
        return bool(requested)
    if _is_mock_env():
        return True
    return not bool(_llm_health(settings).get("ok"))


def _mock_reason(settings: Settings, requested: bool | None, mock: bool) -> str | None:
    if not mock:
        return None
    if requested is True:
        return "请求显式使用离线 Mock 模式（不调用真实 LLM）"
    if requested is None:
        return "未检测到可用的 LLM API key，已自动切换离线 Mock 模式；配置 DEEPSEEK_API_KEY 后可接入真实模型"
    return None


def _server_public(server: Any) -> dict[str, Any]:
    """服务器配置脱敏：绝不含 api_key / 密码 / 环境变量值。"""
    identity = getattr(server, "identity_file", None)
    has_password_env = bool(getattr(server, "password_env", None))
    auth = "ssh-config" if not identity and not has_password_env else ("password-env" if has_password_env else "ssh-key")
    return {
        "name": getattr(server, "name", ""),
        "host": getattr(server, "host", ""),
        "port": int(getattr(server, "port", 22) or 22),
        "user": getattr(server, "user", ""),
        "workdir": getattr(server, "workdir", ""),
        "conda_env": getattr(server, "conda_env", None),
        "scheduler": getattr(server, "scheduler", "none"),
        "gpu_type": getattr(server, "gpu_type", None),
        "partition": getattr(server, "partition", None),
        "tmux_session": getattr(server, "tmux_session", None),
        "identity_file": identity,  # 路径可展示，不读取内容
        "auth": auth,
        "has_password_env": has_password_env,
        "proxy_jump": getattr(server, "proxy_jump", None),
        "python": getattr(server, "python", "python"),
        "max_parallel_jobs": int(getattr(server, "max_parallel_jobs", 2) or 2),
        "auto_mkdir": bool(getattr(server, "auto_mkdir", True)),
    }


def _key_status(provider: Any) -> tuple[str, str]:
    """返回 (状态, 环境变量名)；绝不返回密钥本身。"""
    env_name = str(getattr(provider, "api_key_env", "") or "")
    if getattr(provider, "api_key", None):
        return "config", env_name
    if env_name and os.environ.get(env_name):
        return "env", env_name
    return "missing", env_name


def _provider_public(settings: Settings, name: str, provider: Any) -> dict[str, Any]:
    status, env_name = _key_status(provider)
    return {
        "name": name,
        "type": str(getattr(provider, "type", "openai-compatible")),
        "base_url": str(getattr(provider, "base_url", "")),
        "default_model": str(getattr(provider, "default_model", "")),
        "models": list(getattr(provider, "models", []) or []),
        "api_key_env": env_name or None,
        "key_status": status,
        "is_default": name == settings.llm.default,
        "temperature": getattr(provider, "temperature", None),
        "max_tokens": getattr(provider, "max_tokens", None),
        "supports_tools": bool(getattr(provider, "supports_tools", True)),
        "input_cost_per_mtok": getattr(provider, "input_cost_per_mtok", 0.0),
        "output_cost_per_mtok": getattr(provider, "output_cost_per_mtok", 0.0),
    }


KNOWN_ROLES = ("planner", "executor", "writer", "reviewer", "vision")


def _roles_public(settings: Settings) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {}
    for role in KNOWN_ROLES:
        rc = settings.llm.roles.get(role)
        provider = (getattr(rc, "provider", None) if rc else None) or settings.llm.default
        roles[role] = {
            "provider": provider,
            "model": (getattr(rc, "model", None) if rc else None) or "",
            "explicit_provider": bool(getattr(rc, "provider", None)) if rc else False,
        }
    return roles


def _mask_url(url: str) -> str:
    """日志/错误里避免泄露 URL 里的 user:pass。"""
    return re.sub(r"//[^/@\s]+@", "//***@", str(url))


# ---------------------------------------------------------------------------
# 文献 / fixtures
# ---------------------------------------------------------------------------


def _fixture_paper(entry: dict[str, Any], source: str = "arxiv") -> dict[str, Any]:
    """把 arXiv / daily-arXiv 记录归一化成前端统一卡片结构。"""
    authors_raw = entry.get("authors") or []
    if isinstance(authors_raw, str):
        authors = [part.strip() for part in re.split(r"[,;，]", authors_raw) if part.strip()]
    elif isinstance(authors_raw, list):
        authors = []
        for item in authors_raw:
            if isinstance(item, str):
                authors.append(item)
            elif isinstance(item, dict):
                authors.append(str(item.get("name") or item.get("author") or ""))
        authors = [name for name in authors if name]
    else:
        authors = []

    paper_id = str(entry.get("id") or entry.get("arxiv_id") or entry.get("paper_id") or "")
    summary = str(entry.get("summary") or entry.get("abstract") or "")
    summary_zh = str(entry.get("summary_zh") or entry.get("AI_summary") or entry.get("tldr") or "")
    url = str(entry.get("url") or entry.get("abs_url") or entry.get("arxiv_url") or "")
    if not url and paper_id:
        url = f"https://arxiv.org/abs/{paper_id}"
    pdf_url = str(entry.get("pdf_url") or entry.get("pdf") or "")
    if not pdf_url and paper_id:
        pdf_url = f"https://arxiv.org/pdf/{paper_id}"
    categories = entry.get("categories") or entry.get("primary_category") or []
    if isinstance(categories, str):
        categories = [part.strip() for part in categories.split(",") if part.strip()]
    return {
        "id": paper_id,
        "title": str(entry.get("title") or "").strip(),
        "authors": authors,
        "abstract": summary,
        "summary": summary_zh or summary,
        "summary_zh": summary_zh,
        "published": str(entry.get("published") or entry.get("updated") or "")[:10],
        "venue": str(entry.get("venue") or entry.get("comment") or ""),
        "categories": [str(item) for item in categories],
        "url": url,
        "pdf_url": pdf_url,
        "source": str(entry.get("source") or source),
        "tags": list(entry.get("tags") or []),
    }


def _load_fixture_papers(query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """读取 ``examples/demo/fixtures`` 作为离线演示数据（网络不可用时的兜底）。"""
    papers: list[dict[str, Any]] = []
    search_file = FIXTURES_DIR / "arxiv_search.json"
    if search_file.is_file():
        try:
            raw = json.loads(search_file.read_text(encoding="utf-8"))
            entries = raw.get("results") if isinstance(raw, dict) else raw
            if isinstance(raw, dict) and not entries:
                entries = raw.get("papers") or []
            if isinstance(entries, list):
                papers.extend(_fixture_paper(item, "arxiv") for item in entries if isinstance(item, dict))
        except (OSError, json.JSONDecodeError):
            pass

    daily_file = FIXTURES_DIR / "daily_arxiv.jsonl"
    if daily_file.is_file():
        try:
            for line in daily_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    papers.append(_fixture_paper(item, "daily"))
        except OSError:
            pass

    if query.strip():
        needles = [token.lower() for token in re.split(r"\s+", query.strip()) if token]
        filtered = []
        for paper in papers:
            haystack = " ".join(
                [
                    paper["title"],
                    paper["abstract"],
                    paper["summary"],
                    " ".join(paper["categories"]),
                    " ".join(paper["tags"]),
                ]
            ).lower()
            if all(needle in haystack for needle in needles):
                filtered.append(paper)
        papers = filtered
    return papers[: max(1, limit)]


def _paper_public(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        data = dict(record)
    else:
        data = _jsonable(record)
        if not isinstance(data, dict):
            data = {"raw": data}
    authors = data.get("authors") or []
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.split(",") if part.strip()]
    return {
        "id": str(data.get("id", "")),
        "title": str(data.get("title", "")),
        "authors": [str(a.get("name") if isinstance(a, dict) else a) for a in authors],
        "abstract": str(data.get("abstract", "")),
        "summary": str(data.get("summary", "")),
        "summary_zh": str(data.get("summary", "")),
        "published": str(data.get("published", "")),
        "venue": str(data.get("venue", "")),
        "categories": data.get("categories") or [],
        "url": str(data.get("url", "")),
        "pdf_url": str(data.get("pdf_url", "")),
        "local_pdf": str(data.get("local_pdf", "")),
        "notes_path": str(data.get("notes_path", "")),
        "source": str(data.get("source", "")),
        "added_at": str(data.get("added_at", "")),
        "tags": data.get("tags") or [],
    }


def _skill_meta_public(meta: Any) -> dict[str, Any]:
    if isinstance(meta, dict):
        data = dict(meta)
    else:
        data = _jsonable(meta)
        if not isinstance(data, dict):
            data = {"name": str(data)}
    path = data.get("path")
    directory = data.get("dir")
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    return {
        "name": str(data.get("name", "")),
        "description": str(data.get("description", "")),
        "tags": [str(tag) for tag in tags],
        "version": str(data.get("version", "")),
        "source": str(data.get("source", "bundled")),
        "path": str(path) if path else "",
        "dir": str(directory) if directory else "",
    }


def _scan_bundled_skills() -> list[dict[str, Any]]:
    """SkillManager 未就绪时的只读兜底：扫描 skills_lib/*/SKILL.md 的 frontmatter。"""
    skills_root = REPO_ROOT / "sciforge" / "skills_lib"
    if not skills_root.is_dir():
        return []
    try:
        import yaml
    except Exception:  # noqa: BLE001 - 没有 pyyaml 时退化成正则解析
        yaml = None  # type: ignore[assignment]
    results: list[dict[str, Any]] = []
    for skill_dir in sorted(skills_root.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        meta: dict[str, Any] = {}
        try:
            text = skill_file.read_text(encoding="utf-8")
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
            if match:
                front = match.group(1)
                if yaml is not None:
                    parsed = yaml.safe_load(front)
                    if isinstance(parsed, dict):
                        meta = parsed
                if not meta:
                    name_match = re.search(r"^name:\s*(.+)$", front, re.MULTILINE)
                    desc_match = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
                    ver_match = re.search(r"^version:\s*(.+)$", front, re.MULTILINE)
                    if name_match:
                        meta["name"] = name_match.group(1).strip().strip('"').strip("'")
                    if desc_match:
                        meta["description"] = desc_match.group(1).strip().strip('"').strip("'")
                    if ver_match:
                        meta["version"] = ver_match.group(1).strip().strip('"').strip("'")
            metadata = meta.get("metadata") or {}
            tags = meta.get("tags") or (metadata.get("tags") if isinstance(metadata, dict) else []) or []
            if isinstance(tags, str):
                tags = [part.strip() for part in tags.split(",") if part.strip()]
            results.append(
                {
                    "name": str(meta.get("name") or skill_dir.name),
                    "description": str(meta.get("description") or ""),
                    "tags": [str(tag) for tag in tags],
                    "version": str(meta.get("version") or ""),
                    "source": "bundled",
                    "path": str(skill_file),
                    "dir": str(skill_dir),
                }
            )
        except OSError:
            continue
    return results


# ---------------------------------------------------------------------------
# 离线「工具演示」router
# ---------------------------------------------------------------------------


class _DemoToolRouter:
    """仅在 ``mock=true & demo_tools=true`` 时启用的脚本 router。

    目的：没有 API key 时也能在 Web 端演示「工具调用折叠卡片」。这里**不伪造**
    tool 事件——第 1 步让 AgentLoop 真实调用一次只读工具（read_file / list_dir），
    第 2 步据真实工具结果生成总结；EventBus 上的 tool_call / tool_result 由
    AgentLoop 按正常流程发出。
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.usage = Usage()
        self._step = 0
        self._tool_name, self._arguments = self._pick_plan()

    def _pick_plan(self) -> tuple[str, dict[str, Any]]:
        candidates: list[tuple[str, dict[str, Any]]] = [
            (
                "examples/demo/fixtures/paper.txt",
                {"path": "examples/demo/fixtures/paper.txt", "offset": 1, "limit": 26},
            ),
            (
                "examples/demo/fixtures/arch_spec.yaml",
                {"path": "examples/demo/fixtures/arch_spec.yaml", "offset": 1, "limit": 24},
            ),
            ("README.md", {"path": "README.md", "offset": 1, "limit": 30}),
        ]
        for relative, arguments in candidates:
            if (self.project_dir / relative).is_file():
                return "read_file", arguments
        return "list_dir", {"path": ".", "depth": 2}

    def chat(self, role: str, messages: list[Any], **kwargs: Any) -> LLMResponse:
        self._step += 1
        if self._step == 1:
            call = ToolCall(
                id=f"demo-{uuid.uuid4().hex[:8]}",
                name=self._tool_name,
                arguments=dict(self._arguments),
            )
            usage = Usage(prompt_tokens=64, completion_tokens=32, total_tokens=96, calls=1)
            self.usage.add(usage)
            return LLMResponse(
                content="为了演示工具调用卡片，我先读取一个离线 demo fixture 文件。",
                tool_calls=[call],
                usage=usage,
                model="demo-tool-router",
            )

        tool_messages = [message for message in messages if getattr(message, "role", "") == "tool"]
        preview = str(getattr(tool_messages[-1], "content", "")) if tool_messages else ""
        body = (
            "## 🧰 工具调用演示（离线 · 真实执行）\n\n"
            f"Agent 真实调用了 `{self._tool_name}`，参数 "
            f"`{json.dumps(self._arguments, ensure_ascii=False)}`。"
            "参数、结果预览与耗时都在上方可折叠卡片里，点击卡片展开查看。\n\n"
            "> 说明：这是显式的 demo 路径——工具确实被执行（只读），只是 LLM 侧用脚本"
            "替代，以便在没有 API key 时展示 Web 控制台的工具调用视觉。\n\n"
            f"### 工具返回预览（{self._tool_name}）\n\n```\n{preview[:900]}\n```\n"
        )
        usage = Usage(prompt_tokens=128, completion_tokens=96, total_tokens=224, calls=1)
        self.usage.add(usage)
        return LLMResponse(content=body, tool_calls=[], usage=usage, model="demo-tool-router")


# ---------------------------------------------------------------------------
# App 工厂
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建 FastAPI 应用。settings 为空时按全局/项目配置自动加载。"""
    settings = settings or get_settings()
    project_dir = _resolve_project_dir(settings)

    app = FastAPI(
        title="SciForge Console",
        description="SciForge 本地科研智能体控制台（离线可用、无 CDN 依赖）",
        version=_version(),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.project_dir = project_dir
    app.state.sessions: dict[str, dict[str, Any]] = {}
    app.state.pipeline_runs: dict[str, dict[str, Any]] = {}
    app.state.lock = threading.RLock()
    app.state.started_at = time.time()
    _bootstrap_tools()

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Content-Security-Policy", _CSP)
        return response

    _register_api_routes(app, settings, project_dir)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index_page():  # noqa: ANN202
        index_file = STATIC_DIR / "index.html"
        if not index_file.is_file():
            raise HTTPException(status_code=503, detail="静态页面未安装（sciforge/web/static/index.html 缺失）")
        return FileResponse(index_file, media_type="text/html; charset=utf-8")

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():  # noqa: ANN202
        icon = STATIC_DIR / "favicon.svg"
        if not icon.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(icon, media_type="image/svg+xml")

    return app


def _register_api_routes(app: FastAPI, settings: Settings, project_dir: Path) -> None:
    """注册所有 /api 与产物文件端点。"""

    # ---------------------------- 基础信息 ----------------------------
    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        tool_info = _tool_health()
        llm_info = _llm_health(settings)
        servers = [_server_public(server) for server in settings.servers]
        features = {
            "library": _module_available("sciforge.library"),
            "skills_manager": _module_available("sciforge.skills_manager"),
            "pipeline": _module_available("sciforge.pipeline.engine"),
            "figures": _module_available("sciforge.figures.arch"),
            "writing": _module_available("sciforge.writing.paper"),
            "remote": _module_available("sciforge.tools.remote"),
        }
        return {
            "ok": True,
            "version": _version(),
            "mock": _auto_mock(settings, None),
            "mock_env": _is_mock_env(),
            "llm": llm_info,
            "tools": tool_info,
            "servers": {"count": len(servers), "names": [server["name"] for server in servers]},
            "features": features,
            "project": {
                "name": settings.project_name,
                "dir": str(project_dir),
                "runs_dir": str(project_dir / ".sciforge" / "runs"),
            },
            "fixtures": {
                "available": FIXTURES_DIR.is_dir(),
                "dir": str(FIXTURES_DIR),
            },
            "web": {"host": settings.web.host, "port": settings.web.port, "uptime": round(time.time() - app.state.started_at, 1)},
            "time": _now_iso(),
        }

    @app.get("/api/tools")
    def api_tools(tag: str | None = None) -> dict[str, Any]:
        entries = _tool_entries()
        if tag:
            entries = [entry for entry in entries if tag in entry["tags"]]
        health = _tool_health()
        return {
            "ok": True,
            "available": True,
            "count": len(entries),
            "tools": entries,
            "by_tag": health.get("by_tag", {}),
            "import_errors": health.get("import_errors", {}),
        }

    @app.get("/api/recipes")
    def api_recipes() -> dict[str, Any]:
        default_recipes = [
            {"name": "full", "title": "全流程", "description": "文献 → 精读 → 创新点 → 实验 → 结果 → 论文 → 配图 → 自检"},
            {"name": "survey", "title": "文献调研", "description": "检索 + 精读 + 研究空白"},
            {"name": "experiment", "title": "从想法到实验", "description": "实验设计 → 代码 → GPU 运行 → 分析"},
            {"name": "paper", "title": "从结果到论文", "description": "分析 → 写作 → 配图 → 审稿自检"},
            {"name": "daily", "title": "每日 arXiv", "description": "daily-arXiv-ai-enhanced 速览"},
            {"name": "figure", "title": "论文配图", "description": "架构图 / 曲线 / 三线表"},
            {"name": "litreview", "title": "多论文精读对比", "description": "检索 + 对比矩阵"},
        ]
        try:
            from sciforge.pipeline.recipes import list_recipes

            return {"ok": True, "available": True, "recipes": list_recipes()}
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": True,
                "available": False,
                "error": f"Pipeline recipes 未就绪：{type(exc).__name__}: {exc}",
                "recipes": default_recipes,
            }

    # ---------------------------- 对话 SSE ----------------------------
    @app.post("/api/chat")
    def api_chat(req: ChatRequest):  # noqa: ANN202
        message = (req.message or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message 不能为空")
        if len(message) > 20000:
            raise HTTPException(status_code=413, detail="message 过长（上限 20000 字符）")
        session_id = _clean_session_id(req.session_id) or _new_session_id()
        mock = _auto_mock(settings, req.mock)

        event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=20000)
        bus = EventBus()
        state = {"closed": False}
        dedupe = {"sig": None, "ts": 0.0}
        dedupe_lock = threading.Lock()

        def on_event(event: Event) -> None:
            try:
                sig = (event.kind, json.dumps(event.payload, sort_keys=True, default=str, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                sig = (event.kind, repr(event.payload))
            now = time.monotonic()
            with dedupe_lock:
                # AgentLoop 同时经 ctx.emit 与 bus.emit 发事件，会造成同 payload 的瞬时重复；
                # 这里做 0.5s 窗口去重，保证前端每类事件只收到一次。
                if sig == dedupe["sig"] and (now - dedupe["ts"]) < 0.5:
                    dedupe["ts"] = now
                    return
                dedupe["sig"] = sig
                dedupe["ts"] = now
            if state["closed"]:
                return
            try:
                event_queue.put_nowait(
                    {"kind": event.kind, "payload": _jsonable(event.payload, max_str=6000), "ts": event.ts}
                )
            except queue.Full:
                pass

        unsubscribe = bus.subscribe(on_event)

        history: list[Any] = []
        with app.state.lock:
            stored = app.state.sessions.get(session_id)
            if stored:
                history = list(stored.get("messages") or [])

        def worker() -> None:
            started_at = time.monotonic()
            try:
                from sciforge.agent.runtime import make_agent

                demo_router = None
                if req.demo_tools and mock:
                    try:
                        demo_router = _DemoToolRouter(project_dir)
                    except Exception:  # noqa: BLE001 - demo 不可用时退回普通 mock
                        demo_router = None
                agent, _ctx, _router = make_agent(
                    settings,
                    project_dir=project_dir,
                    session_id=session_id,
                    bus=bus,
                    router=demo_router,
                    mock=mock,
                    stream=True,
                    max_steps=req.max_steps,
                )
                result = agent.run(message, history=history or None)
                _store_history(app, session_id, result)
                done_payload = {
                    "ok": result.error is None,
                    "session_id": session_id,
                    "mock": mock,
                    "steps": result.steps,
                    "tool_calls": result.tool_calls,
                    "stopped_reason": result.stopped_reason,
                    "usage": result.usage.as_dict(),
                    "content": result.content,
                    "artifacts": _rel_artifacts(project_dir, result.artifacts),
                    "error": result.error,
                    "duration": round(time.monotonic() - started_at, 2),
                }
                event_queue.put({"kind": "done", "payload": done_payload})
            except Exception as exc:  # noqa: BLE001 - 任何失败都要让前端收到明确错误
                message_text = f"Agent 执行失败：{type(exc).__name__}: {exc}"
                event_queue.put({"kind": "error", "payload": {"message": message_text, "where": "web"}})
                event_queue.put(
                    {
                        "kind": "done",
                        "payload": {
                            "ok": False,
                            "session_id": session_id,
                            "mock": mock,
                            "error": message_text,
                        },
                    }
                )
            finally:
                state["closed"] = True

        thread = threading.Thread(target=worker, name=f"sciforge-chat-{session_id}", daemon=True)
        thread.start()

        def stream() -> Iterator[str]:
            yield _sse(
                {
                    "session_id": session_id,
                    "mock": mock,
                    "demo_tools": bool(req.demo_tools and mock),
                    "mock_reason": _mock_reason(settings, req.mock, mock),
                    "project_dir": str(project_dir),
                },
                event="meta",
            )
            started = time.monotonic()
            last_ping = started
            try:
                while True:
                    try:
                        item = event_queue.get(timeout=1.0)
                    except queue.Empty:
                        now = time.monotonic()
                        if not thread.is_alive() and event_queue.empty():
                            yield _sse({"message": "Agent 线程意外结束（无 done 事件）"}, event="error")
                            yield _sse({"ok": False, "session_id": session_id, "error": "agent_thread_exit"}, event="done")
                            break
                        if now - started > CHAT_TIMEOUT_SECONDS:
                            yield _sse(
                                {"message": f"对话等待超时（>{int(CHAT_TIMEOUT_SECONDS)}s），已主动结束 SSE 流"},
                                event="error",
                            )
                            yield _sse({"ok": False, "session_id": session_id, "error": "timeout"}, event="done")
                            break
                        if now - last_ping >= SSE_HEARTBEAT_SECONDS:
                            last_ping = now
                            yield ": ping\n\n"
                        continue
                    if item["kind"] == "done":
                        yield _sse(item["payload"], event="done")
                        break
                    yield _sse(item["payload"], event=item["kind"])
            except GeneratorExit:
                return
            finally:
                unsubscribe()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------------------------- 文献 ----------------------------
    @app.get("/api/library")
    def api_library(query: str | None = None, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        try:
            from sciforge.library import PaperLibrary
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": True,
                "available": False,
                "degraded": True,
                "error": f"文献库模块未就绪（sciforge.library）：{type(exc).__name__}: {exc}",
                "query": query or "",
                "total": 0,
                "papers": [],
            }
        try:
            library = PaperLibrary(ensure_dirs(settings))
            records = library.search(query, limit=limit) if query else library.list(limit=limit)
            papers = [_paper_public(record) for record in records]
            return {"ok": True, "available": True, "query": query or "", "total": len(papers), "papers": papers}
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "available": True,
                "error": f"文献库读取失败：{type(exc).__name__}: {exc}",
                "query": query or "",
                "total": 0,
                "papers": [],
            }

    @app.get("/api/literature/search")
    def api_literature_search(query: str = "", limit: int = 12) -> dict[str, Any]:
        """文献搜索：优先真实 lit_search 工具；未就绪时返回 demo fixtures（离线演示）。"""
        limit = max(1, min(int(limit), 50))
        query = (query or "").strip()
        if query:
            try:
                from sciforge.agent.runtime import make_ctx
                from sciforge.core.registry import get_tool, run_tool

                tool = get_tool("lit_search")
                if tool is not None:
                    ctx = make_ctx(
                        settings,
                        project_dir=project_dir,
                        session_id=f"web-lit-{uuid.uuid4().hex[:8]}",
                    )
                    result = run_tool("lit_search", ctx, {"query": query, "limit": limit, "source": "arxiv"})
                    if result.ok:
                        data = result.data or {}
                        raw_papers = data.get("papers") or data.get("results") or []
                        papers = [
                            _fixture_paper(item, str(data.get("source") or "arxiv"))
                            for item in raw_papers
                            if isinstance(item, dict)
                        ]
                        if papers:
                            return {
                                "ok": True,
                                "available": True,
                                "source": "lit_search",
                                "query": query,
                                "total": len(papers),
                                "papers": papers[:limit],
                                "content": result.content[:4000],
                            }
                    else:
                        tool_error = result.error or result.content
                else:
                    tool_error = "lit_search 工具未注册"
            except Exception as exc:  # noqa: BLE001 - 真实工具失败时回退到 fixtures
                tool_error = f"{type(exc).__name__}: {exc}"
        else:
            tool_error = "未提供查询词"

        papers = _load_fixture_papers(query, limit)
        if query:
            notice = "当前展示的是 examples/demo/fixtures 中的离线演示数据"
            if tool_error:
                notice += f"（真实检索不可用：{tool_error}）"
        else:
            notice = (
                "当前展示的是 examples/demo/fixtures 中的离线演示数据："
                "9 篇 arXiv 真实论文元数据 + 3 条 daily-arXiv-ai-enhanced 中文速览"
            )
        return {
            "ok": True,
            "available": False,
            "degraded": True,
            "source": "demo-fixture",
            "query": query,
            "total": len(papers),
            "papers": papers,
            "notice": notice,
        }

    @app.post("/api/library/add")
    def api_library_add(payload: dict[str, Any]) -> dict[str, Any]:
        record = payload.get("paper") if isinstance(payload.get("paper"), dict) else payload
        paper_id = str(record.get("id") or record.get("arxiv_id") or "").strip()
        title = str(record.get("title") or "").strip()
        if not paper_id or not title:
            raise HTTPException(status_code=400, detail="需要 id 与 title")
        try:
            from sciforge.library import PaperLibrary, PaperRecord
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "available": False,
                "error": f"文献库模块未就绪（sciforge.library）：{type(exc).__name__}: {exc}",
            }
        try:
            authors = record.get("authors") or []
            categories = record.get("categories") or []
            tags = record.get("tags") or []
            paper = PaperRecord(
                id=paper_id,
                title=title,
                authors=[str(a.get("name") if isinstance(a, dict) else a) for a in authors],
                abstract=str(record.get("abstract") or ""),
                url=str(record.get("url") or ""),
                pdf_url=str(record.get("pdf_url") or ""),
                published=str(record.get("published") or ""),
                venue=str(record.get("venue") or ""),
                categories=[str(c) for c in categories],
                summary=str(record.get("summary") or record.get("summary_zh") or ""),
                tags=[str(t) for t in tags],
                source=str(record.get("source") or "manual"),
                added_at=str(record.get("added_at") or _now_iso()),
            )
            library = PaperLibrary(ensure_dirs(settings))
            saved = library.add(paper)
            return {"ok": True, "available": True, "paper": _paper_public(saved)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "available": True, "error": f"入库失败：{type(exc).__name__}: {exc}"}

    @app.post("/api/paper/read")
    def api_paper_read(payload: dict[str, Any]) -> dict[str, Any]:
        """精读入口：优先调用 paper_read 工具；未就绪时返回明确提示（前端引导走对话）。"""
        target = str(payload.get("paper_id") or payload.get("path") or payload.get("url") or "").strip()
        try:
            from sciforge.agent.runtime import make_ctx
            from sciforge.core.registry import get_tool, run_tool

            tool = get_tool("paper_read")
            if tool is None:
                return {
                    "ok": False,
                    "available": False,
                    "error": "精读工具（T1 paper_read）尚未注册；可在对话页发送「精读这篇论文：<标题>」走 Agent 流程",
                }
            ctx = make_ctx(settings, project_dir=project_dir, session_id=f"web-read-{uuid.uuid4().hex[:8]}")
            result = run_tool("paper_read", ctx, {"paper": target})
            return {
                "ok": bool(result.ok),
                "available": True,
                "content": result.content[:20000],
                "data": _jsonable(result.data, max_str=8000),
                "artifacts": _rel_artifacts(project_dir, result.artifacts),
                "error": result.error,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "available": False, "error": f"精读失败：{type(exc).__name__}: {exc}"}

    # ---------------------------- 服务器 / GPU ----------------------------
    @app.get("/api/servers")
    def api_servers() -> dict[str, Any]:
        servers = [_server_public(server) for server in settings.servers]
        return {
            "ok": True,
            "available": True,
            "count": len(servers),
            "servers": servers,
            "gpu_tool": _module_available("sciforge.tools.remote"),
            "notice": None if servers else "尚未配置 GPU 服务器。可在 ~/.sciforge/config.toml 添加 [[servers]]，或运行 `sciforge ssh add`。",
        }

    @app.get("/api/gpu")
    def api_gpu(server: str | None = None) -> dict[str, Any]:
        try:
            from sciforge.core.registry import get_tool, run_tool
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "available": False, "error": f"工具注册表不可用：{exc}"}
        tool = get_tool("gpu_status")
        if tool is None:
            return {
                "ok": False,
                "available": False,
                "error": "远程执行模块（T2 gpu_status）未就绪，暂无实时 GPU 数据",
                "server": server,
                "gpus": [],
            }
        box: dict[str, Any] = {}
        sid = f"web-gpu-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

        def probe() -> None:
            try:
                from sciforge.agent.runtime import make_ctx

                ctx = make_ctx(settings, project_dir=project_dir, session_id=sid)
                args: dict[str, Any] = {}
                if server:
                    args["server"] = server
                box["result"] = run_tool("gpu_status", ctx, args)
            except Exception as exc:  # noqa: BLE001
                box["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=probe, name="sciforge-gpu-probe", daemon=True)
        thread.start()
        thread.join(timeout=GPU_PROBE_TIMEOUT)
        if thread.is_alive():
            return {
                "ok": False,
                "available": True,
                "server": server,
                "error": f"GPU 探测超时（>{int(GPU_PROBE_TIMEOUT)}s），请检查服务器 SSH 连通性",
                "gpus": [],
            }
        if "error" in box:
            return {"ok": False, "available": True, "server": server, "error": box["error"], "gpus": []}
        result = box.get("result")
        if result is None:
            return {"ok": False, "available": True, "server": server, "error": "GPU 探测无返回", "gpus": []}
        gpus = _normalize_gpus(getattr(result, "data", None))
        return {
            "ok": bool(getattr(result, "ok", False)),
            "available": True,
            "server": server,
            "gpus": gpus,
            "content": str(getattr(result, "content", ""))[:6000],
            "error": getattr(result, "error", None),
            "data": _jsonable(getattr(result, "data", None), max_str=4000),
        }

    # ---------------------------- 大模型配置（GUI） ----------------------------
    @app.get("/api/llm")
    def api_llm_config() -> dict[str, Any]:
        return {
            "ok": True,
            "available": True,
            "default": settings.llm.default,
            "providers": [
                _provider_public(settings, name, cfg) for name, cfg in settings.llm.providers.items()
            ],
            "roles": _roles_public(settings),
            "env_hint": "DEEPSEEK_API_KEY",
            "config_path": str(global_config_path()),
            "mock_env": _is_mock_env(),
            "note": "API Key 只会写入本机 ~/.sciforge/config.toml（0600），不会回传浏览器。",
        }

    @app.post("/api/llm/providers")
    def api_llm_provider_save(form: ProviderForm) -> dict[str, Any]:
        name = (form.name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name):
            raise HTTPException(status_code=400, detail="Provider 名称只能包含字母/数字/下划线/点/短横线（1-40 字符）")
        base_url = (form.base_url or "").strip().rstrip("/")
        if not re.match(r"^https?://", base_url):
            raise HTTPException(status_code=400, detail="Base URL 必须以 http:// 或 https:// 开头")
        default_model = (form.default_model or "").strip()
        if not default_model:
            raise HTTPException(status_code=400, detail="默认模型不能为空")
        raw_models = form.models
        if isinstance(raw_models, str):
            models = [item.strip() for item in re.split(r"[,，\n]", raw_models) if item.strip()]
        else:
            models = [str(item).strip() for item in (raw_models or []) if str(item).strip()]
        if default_model not in models:
            models = [default_model, *[item for item in models if item != default_model]]

        existing = settings.llm.providers.get(name)
        provider_kwargs: dict[str, Any] = {
            "type": (form.type or "openai-compatible").strip() or "openai-compatible",
            "base_url": base_url,
            "default_model": default_model,
            "models": models,
            "api_key_env": (form.api_key_env or "").strip() or None,
            "supports_tools": bool(form.supports_tools),
        }
        if form.temperature is not None:
            provider_kwargs["temperature"] = form.temperature
        elif existing is not None:
            provider_kwargs["temperature"] = existing.temperature
        if form.max_tokens is not None:
            provider_kwargs["max_tokens"] = form.max_tokens
        elif existing is not None:
            provider_kwargs["max_tokens"] = existing.max_tokens
        if form.input_cost_per_mtok is not None:
            provider_kwargs["input_cost_per_mtok"] = form.input_cost_per_mtok
        elif existing is not None:
            provider_kwargs["input_cost_per_mtok"] = existing.input_cost_per_mtok
        if form.output_cost_per_mtok is not None:
            provider_kwargs["output_cost_per_mtok"] = form.output_cost_per_mtok
        elif existing is not None:
            provider_kwargs["output_cost_per_mtok"] = existing.output_cost_per_mtok

        # Key 规则：clear_api_key 优先；填写了新 Key 才覆盖；留空则保留原值。
        if form.clear_api_key:
            provider_kwargs["api_key"] = None
        elif form.api_key is not None and form.api_key.strip():
            provider_kwargs["api_key"] = form.api_key.strip()
        elif existing is not None:
            provider_kwargs["api_key"] = existing.api_key
        else:
            provider_kwargs["api_key"] = None

        provider = ProviderConfig(**provider_kwargs)
        settings.llm.providers[name] = provider
        if not settings.llm.default or settings.llm.default not in settings.llm.providers:
            settings.llm.default = name
        if form.make_default:
            settings.llm.default = name
        save_global_config(settings)
        return {
            "ok": True,
            "available": True,
            "provider": _provider_public(settings, name, provider),
            "default": settings.llm.default,
            "path": str(global_config_path()),
            "note": "已保存到本机 ~/.sciforge/config.toml（0600）",
        }

    @app.delete("/api/llm/providers/{name}")
    def api_llm_provider_delete(name: str) -> dict[str, Any]:
        if name not in settings.llm.providers:
            raise HTTPException(status_code=404, detail=f"Provider 不存在：{name}")
        if len(settings.llm.providers) <= 1:
            raise HTTPException(status_code=400, detail="至少保留一个 Provider，无法删除最后一个")
        del settings.llm.providers[name]
        for _role, rc in list(settings.llm.roles.items()):
            if getattr(rc, "provider", None) == name:
                rc.provider = None
                rc.model = None
        if settings.llm.default == name:
            settings.llm.default = next(iter(settings.llm.providers))
        save_global_config(settings)
        return {
            "ok": True,
            "available": True,
            "default": settings.llm.default,
            "providers": [
                _provider_public(settings, key, cfg) for key, cfg in settings.llm.providers.items()
            ],
            "roles": _roles_public(settings),
        }

    @app.post("/api/llm/roles")
    def api_llm_roles_save(form: RolesForm) -> dict[str, Any]:
        for role, entry in form.roles.items():
            if role not in KNOWN_ROLES:
                raise HTTPException(status_code=400, detail=f"未知角色：{role}")
            provider = (entry.provider or "").strip()
            if provider and provider not in settings.llm.providers:
                raise HTTPException(status_code=400, detail=f"角色 {role} 指向未配置的 Provider：{provider}")
            settings.llm.roles[role] = RoleConfig(
                provider=provider or None,
                model=(entry.model or "").strip() or None,
            )
        save_global_config(settings)
        return {
            "ok": True,
            "available": True,
            "roles": _roles_public(settings),
            "path": str(global_config_path()),
        }

    @app.post("/api/llm/test")
    def api_llm_test(req: LlmTestRequest) -> dict[str, Any]:
        role = (req.role or "").strip() or None
        provider_name = (req.provider or "").strip() or (
            settings.llm.provider_name_for(role) if role else settings.llm.default
        )
        cfg = settings.llm.providers.get(provider_name)
        if cfg is None:
            raise HTTPException(status_code=404, detail=f"Provider 不存在：{provider_name}")
        api_key = settings.resolve_api_key(provider_name)
        if not api_key:
            env_name = cfg.api_key_env or "DEEPSEEK_API_KEY"
            return {
                "ok": False,
                "available": True,
                "provider": provider_name,
                "error": f"缺少 API Key：请在表单填写 Key，或 export {env_name}",
            }
        role_cfg = settings.llm.roles.get(role) if role else None
        model = (req.model or "").strip() or (
            getattr(role_cfg, "model", None) if role_cfg else None
        ) or cfg.default_model
        prompt = (req.prompt or "用一句话说明你是谁。").strip()[:2000]
        box: dict[str, Any] = {}
        started = time.monotonic()

        def probe() -> None:
            try:
                from sciforge.llm.openai_compat import OpenAICompatProvider

                provider = OpenAICompatProvider(provider_name, cfg, api_key=api_key)
                response = provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    model=model,
                    max_tokens=min(int(cfg.max_tokens or 4096), 256),
                )
                box["response"] = response
            except Exception as exc:  # noqa: BLE001
                box["error"] = f"{type(exc).__name__}: {_mask_url(str(exc))[:600]}"

        thread = threading.Thread(target=probe, name="sciforge-llm-test", daemon=True)
        thread.start()
        thread.join(timeout=45)
        if thread.is_alive():
            return {
                "ok": False,
                "available": True,
                "provider": provider_name,
                "model": model,
                "error": "LLM 测试超时（>45s）：请检查 Base URL / 网络 / 代理",
            }
        if "error" in box:
            return {"ok": False, "available": True, "provider": provider_name, "model": model, "error": box["error"]}
        response = box.get("response")
        return {
            "ok": True,
            "available": True,
            "provider": provider_name,
            "model": model,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "content": (getattr(response, "content", "") or "")[:1200],
            "usage": _jsonable(getattr(response, "usage", None), max_str=2000),
        }

    # ---------------------------- 文献检索设置（GUI） ----------------------------
    @app.get("/api/literature/config")
    def api_literature_config() -> dict[str, Any]:
        lit = settings.literature
        has_config_key = bool(getattr(lit, "s2_api_key", None))
        env_name = str(getattr(lit, "s2_api_key_env", "S2_API_KEY") or "S2_API_KEY")
        has_env_key = bool(os.environ.get(env_name))
        return {
            "ok": True,
            "available": True,
            "email": getattr(lit, "email", None) or "",
            "unpaywall_email": getattr(lit, "unpaywall_email", None) or "",
            "s2_api_key_env": env_name,
            "s2_key_status": "env" if has_env_key else ("config" if has_config_key else "missing"),
            "daily_arxiv_language": getattr(lit, "daily_arxiv_language", "Chinese"),
            "arxiv_categories": list(getattr(lit, "arxiv_categories", []) or []),
            "config_path": str(global_config_path()),
            "sources": [
                {"id": "arxiv", "name": "arXiv", "need_key": False, "note": "预印本全文，最稳定"},
                {"id": "daily", "name": "daily-arXiv-ai-enhanced", "need_key": False, "note": "每日 AI 中文摘要"},
                {"id": "openalex", "name": "OpenAlex", "need_key": False, "note": "跨库元数据 + OA 链接"},
                {"id": "crossref", "name": "Crossref", "need_key": False, "note": "DOI / BibTeX 权威元数据"},
                {"id": "hf_papers", "name": "HF Papers", "need_key": False, "note": "HF 每日/趋势论文"},
                {"id": "semantic_scholar", "name": "Semantic Scholar", "need_key": True, "note": "引用图/摘要；匿名 429，建议配 Key"},
            ],
            "note": "S2 Key 写入本机 ~/.sciforge/config.toml（0600），env 变量优先；不会回传浏览器。",
        }

    @app.post("/api/literature/config")
    def api_literature_config_save(form: LiteratureForm) -> dict[str, Any]:
        language = (form.daily_arxiv_language or "Chinese").strip()
        if language not in ("Chinese", "English"):
            raise HTTPException(status_code=400, detail="daily_arxiv_language 只能是 Chinese 或 English")
        email = (form.email or "").strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(status_code=400, detail="邮箱格式不正确")
        lit = settings.literature
        lit.email = email or None
        lit.unpaywall_email = (form.unpaywall_email or "").strip() or None
        lit.s2_api_key_env = (form.s2_api_key_env or "").strip() or "S2_API_KEY"
        lit.daily_arxiv_language = language
        if form.clear_s2_api_key:
            lit.s2_api_key = None
        elif form.s2_api_key is not None and form.s2_api_key.strip():
            lit.s2_api_key = form.s2_api_key.strip()
        save_global_config(settings)
        return {
            "ok": True,
            "available": True,
            "email": lit.email or "",
            "unpaywall_email": lit.unpaywall_email or "",
            "s2_api_key_env": lit.s2_api_key_env,
            "s2_key_status": "env" if os.environ.get(lit.s2_api_key_env or "S2_API_KEY") else (
                "config" if lit.s2_api_key else "missing"
            ),
            "daily_arxiv_language": lit.daily_arxiv_language,
            "path": str(global_config_path()),
            "note": "已保存到本机 ~/.sciforge/config.toml（0600）",
        }

    # ---------------------------- GPU 服务器配置（GUI） ----------------------------
    @app.post("/api/servers")
    def api_server_save(form: ServerForm) -> dict[str, Any]:
        name = (form.name or "").strip()
        host = (form.host or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name):
            raise HTTPException(status_code=400, detail="服务器名称只能包含字母/数字/下划线/点/短横线（1-40 字符）")
        if not host:
            raise HTTPException(status_code=400, detail="host 不能为空")
        if form.scheduler not in ("none", "slurm"):
            raise HTTPException(status_code=400, detail="scheduler 只能是 none 或 slurm")
        try:
            server = ServerConfig(**form.model_dump())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"服务器配置不合法：{exc}") from exc
        servers = [existing for existing in settings.servers if existing.name != name]
        servers.append(server)
        settings.servers = servers
        save_global_config(settings)
        return {
            "ok": True,
            "available": True,
            "server": _server_public(server),
            "count": len(settings.servers),
            "servers": [_server_public(item) for item in settings.servers],
            "path": str(global_config_path()),
            "note": "已保存到本机 ~/.sciforge/config.toml（0600）",
        }

    @app.delete("/api/servers/{name}")
    def api_server_delete(name: str) -> dict[str, Any]:
        remaining = [server for server in settings.servers if server.name != name]
        if len(remaining) == len(settings.servers):
            raise HTTPException(status_code=404, detail=f"服务器不存在：{name}")
        settings.servers = remaining
        save_global_config(settings)
        return {
            "ok": True,
            "available": True,
            "count": len(remaining),
            "servers": [_server_public(item) for item in remaining],
        }

    @app.post("/api/servers/{name}/test")
    def api_server_test(name: str) -> dict[str, Any]:
        server = settings.server(name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"服务器不存在：{name}")
        try:
            from sciforge.tools.remote import SSHExecutor
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "available": False,
                "server": name,
                "error": f"远程执行模块未就绪：{type(exc).__name__}: {exc}",
            }
        started = time.monotonic()
        box: dict[str, Any] = {}

        def probe() -> None:
            try:
                box["result"] = SSHExecutor(server).check(timeout=20)
            except Exception as exc:  # noqa: BLE001
                box["error"] = f"{type(exc).__name__}: {_mask_url(str(exc))[:600]}"

        thread = threading.Thread(target=probe, name="sciforge-ssh-test", daemon=True)
        thread.start()
        thread.join(timeout=30)
        if thread.is_alive():
            return {
                "ok": False,
                "available": True,
                "server": name,
                "error": "SSH 探测超时（>30s）：请检查 host/端口/VPN/私钥",
                "latency_ms": 30000,
            }
        if "error" in box:
            return {"ok": False, "available": True, "server": name, "error": box["error"]}
        info = box.get("result") or {}
        return {
            "ok": bool(info.get("ok")),
            "available": True,
            "server": name,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "info": _jsonable(info, max_str=3000),
            "error": info.get("error"),
        }

    # ---------------------------- Pipeline ----------------------------
    @app.post("/api/pipeline/run")
    def api_pipeline_run(req: PipelineRunRequest) -> dict[str, Any]:
        topic = (req.topic or "").strip()
        if not topic:
            raise HTTPException(status_code=400, detail="topic 不能为空")
        recipe = (req.recipe or settings.pipeline.default_recipe or "full").strip()
        try:
            from sciforge.pipeline.engine import PipelineRunner  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "available": False,
                "error": f"Pipeline 引擎未就绪（sciforge.pipeline.engine）：{type(exc).__name__}: {exc}",
            }
        mock = _auto_mock(settings, req.mock)
        api_run_id = f"web-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        bus = EventBus()
        record: dict[str, Any] = {
            "api_run_id": api_run_id,
            "pipeline_run_id": None,
            "recipe": recipe,
            "topic": topic,
            "mock": mock,
            "status": "running",
            "started_at": _now_iso(),
            "started_ts": time.time(),
            "finished_at": None,
            "duration": None,
            "error": None,
            "stages": [],
            "artifacts": [],
            "events": [],
            "run_dir": None,
        }

        def on_event(event: Event) -> None:
            with app.state.lock:
                if len(record["events"]) < 400:
                    record["events"].append(
                        {"kind": event.kind, "payload": _jsonable(event.payload, max_str=2000), "ts": event.ts}
                    )

        bus.subscribe(on_event)
        with app.state.lock:
            app.state.pipeline_runs[api_run_id] = record

        def worker() -> None:
            try:
                from sciforge.pipeline.engine import PipelineRunner

                runner = PipelineRunner(settings, project_dir, mock=mock, bus=bus)
                run = runner.run(recipe, topic, until=req.until)
                data = _jsonable(run, max_str=20000)
                run_dir = None
                pipeline_run_id = None
                if isinstance(data, dict):
                    pipeline_run_id = str(data.get("run_id") or "") or None
                    raw_dir = data.get("run_dir")
                    if raw_dir:
                        run_dir = Path(str(raw_dir))
                with app.state.lock:
                    record["status"] = str(data.get("status", "done")) if isinstance(data, dict) else "done"
                    record["pipeline_run_id"] = pipeline_run_id
                    record["stages"] = data.get("stages", []) if isinstance(data, dict) else []
                    record["result"] = data
                    record["error"] = data.get("error") if isinstance(data, dict) else None
                    if run_dir is not None:
                        record["run_dir"] = _rel_posix(project_dir, run_dir)
                        record["artifacts"] = _scan_files(project_dir, run_dir)
            except Exception as exc:  # noqa: BLE001
                with app.state.lock:
                    record["status"] = "failed"
                    record["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                with app.state.lock:
                    record["finished_at"] = _now_iso()
                    record["duration"] = round(time.time() - float(record.get("started_ts") or time.time()), 1)

        thread = threading.Thread(target=worker, name=f"sciforge-pipeline-{api_run_id}", daemon=True)
        thread.start()
        return {
            "ok": True,
            "available": True,
            "run_id": api_run_id,
            "mock": mock,
            "mock_reason": _mock_reason(settings, req.mock, mock),
            "recipe": recipe,
            "topic": topic,
            "status": "running",
            "poll": f"/api/pipeline/{api_run_id}",
        }

    @app.get("/api/pipeline/{run_id}")
    def api_pipeline_status(run_id: str) -> dict[str, Any]:
        run_id = run_id.strip()
        with app.state.lock:
            record = app.state.pipeline_runs.get(run_id)
            if record is not None:
                snapshot = dict(record)
                snapshot["events"] = list(record.get("events") or [])
                snapshot["stages"] = list(record.get("stages") or [])
                snapshot["artifacts"] = list(record.get("artifacts") or [])
        if record is None:
            # 磁盘上的历史 run 也能查
            try:
                from sciforge.pipeline.engine import load_run

                data = load_run(project_dir, run_id)
            except Exception:  # noqa: BLE001
                data = None
            if data is None:
                raise HTTPException(status_code=404, detail=f"未找到 run：{run_id}")
            return {"ok": True, "available": True, "source": "disk", "run": _jsonable(data)}
        artifacts = []
        for rel in snapshot.get("artifacts") or []:
            artifacts.append({"path": rel, "url": f"/files/{quote(rel)}"})
        snapshot["artifact_links"] = artifacts
        return {"ok": True, "available": True, "source": "memory", "run": snapshot}

    @app.get("/api/runs")
    def api_runs(limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        runs: list[dict[str, Any]] = []
        try:
            from sciforge.pipeline.engine import list_runs as list_engine_runs

            raw_runs = list_engine_runs(project_dir)
            for item in raw_runs:
                data = dict(item)
                run_dir = data.get("run_dir")
                rel = _rel_posix(project_dir, run_dir) if run_dir else None
                data["run_dir"] = rel or str(run_dir or "")
                data["report_url"] = f"/files/{quote(rel + '/REPORT.md')}" if rel else None
                runs.append(_jsonable(data, max_str=20000))
        except Exception:  # noqa: BLE001 - 引擎缺失时手动扫描 run.json
            runs_dir = project_dir / ".sciforge" / "runs"
            if runs_dir.is_dir():
                for run_dir in sorted(runs_dir.iterdir(), reverse=True):
                    manifest = run_dir / "run.json"
                    if not manifest.is_file():
                        continue
                    try:
                        data = json.loads(manifest.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if isinstance(data, dict):
                        data["run_dir"] = _rel_posix(project_dir, run_dir) or ""
                        runs.append(_jsonable(data, max_str=20000))
        with app.state.lock:
            active = [
                {
                    "api_run_id": run_id,
                    "run_id": record.get("pipeline_run_id") or run_id,
                    "recipe": record.get("recipe"),
                    "topic": record.get("topic"),
                    "status": record.get("status"),
                    "started_at": record.get("started_at"),
                    "duration": record.get("duration"),
                    "live": record.get("status") == "running",
                }
                for run_id, record in app.state.pipeline_runs.items()
            ]
        return {"ok": True, "available": _module_available("sciforge.pipeline.engine"), "runs": runs[:limit], "active": active}

    # ---------------------------- 技能 ----------------------------
    @app.get("/api/skills")
    def api_skills(query: str = "") -> dict[str, Any]:
        manager = _skill_manager(settings)
        if manager is not None:
            try:
                metas = manager.search(query, limit=200) if query.strip() else manager.list()
                skills = [_skill_meta_public(meta) for meta in metas]
                return {"ok": True, "available": True, "count": len(skills), "skills": skills, "query": query}
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "available": True,
                    "error": f"SkillManager 读取失败：{type(exc).__name__}: {exc}",
                    "skills": [],
                }
        skills = _scan_bundled_skills()
        if query.strip():
            needle = query.strip().lower()
            skills = [
                skill
                for skill in skills
                if needle in (skill["name"] + " " + skill["description"] + " " + " ".join(skill["tags"])).lower()
            ]
        return {
            "ok": True,
            "available": False,
            "degraded": True,
            "count": len(skills),
            "skills": skills,
            "query": query,
            "error": "SkillManager（T4，sciforge.skills_manager）未就绪；当前为 skills_lib 只读扫描结果，「安装到 DSH」不可用",
        }

    @app.post("/api/skills/install-dsh")
    def api_skills_install_dsh(req: SkillInstallRequest) -> dict[str, Any]:
        return _install_skill(settings, req.name, "dsh")

    @app.post("/api/skills/install")
    def api_skills_install(req: SkillInstallRequest) -> dict[str, Any]:
        target = (req.target or "dsh").strip().lower()
        if target not in ("dsh", "claude", "project"):
            raise HTTPException(status_code=400, detail="target 必须是 dsh / claude / project")
        return _install_skill(settings, req.name, target)

    # ---------------------------- 产物 / 文件 ----------------------------
    @app.get("/api/artifacts")
    def api_artifacts(limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add_file(path: Path, kind: str) -> None:
            if len(items) >= limit:
                return
            if not path.is_file() or path.suffix.lower() not in _ARTIFACT_EXTS:
                return
            rel = _rel_posix(project_dir, path)
            if not rel or rel in seen:
                return
            seen.add(rel)
            suffix = path.suffix.lower()
            previewable = suffix in _GALLERY_EXTS
            url = f"/figures/{quote(rel)}" if previewable else f"/files/{quote(rel)}"
            items.append(
                {
                    "path": rel,
                    "name": path.name,
                    "url": url,
                    "kind": kind,
                    "ext": suffix.lstrip("."),
                    "previewable": previewable,
                    "size": path.stat().st_size,
                    "mtime": _now_iso(path.stat().st_mtime),
                }
            )

        for root_rel, kind in (("figures", "figure"), ("results", "data"), ("tables", "table"), ("notes", "doc")):
            root = project_dir / root_rel
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                add_file(path, kind)

        runs_dir = project_dir / ".sciforge" / "runs"
        if runs_dir.is_dir():
            for run_dir in sorted(runs_dir.iterdir(), reverse=True)[:10]:
                if run_dir.is_dir():
                    for path in sorted(run_dir.rglob("*")):
                        add_file(path, "run")

        fixtures: list[dict[str, Any]] = []
        if FIXTURES_DIR.is_dir():
            for path in sorted(FIXTURES_DIR.rglob("*")):
                if path.is_file():
                    rel = _rel_posix(project_dir, path)
                    if rel:
                        suffix = path.suffix.lower()
                        previewable = suffix in _GALLERY_EXTS
                        fixtures.append(
                            {
                                "path": rel,
                                "name": path.name,
                                "url": (f"/figures/{quote(rel)}" if previewable else f"/files/{quote(rel)}"),
                                "kind": "fixture",
                                "ext": suffix.lstrip("."),
                                "previewable": previewable,
                                "size": path.stat().st_size,
                            }
                        )
        return {
            "ok": True,
            "count": len(items),
            "artifacts": items,
            "fixtures": fixtures,
            "runs_dir": str(project_dir / ".sciforge" / "runs"),
        }

    # ---------------------------- 安全文件服务 ----------------------------
    @app.get("/files/{path:path}")
    def api_file(path: str):  # noqa: ANN202
        target = _safe_join(project_dir, path)
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    @app.get("/figures/{path:path}")
    def api_figure(path: str):  # noqa: ANN202
        target = _safe_join(project_dir, path)
        if target.suffix.lower() not in _GALLERY_EXTS:
            raise HTTPException(status_code=404, detail="不是可预览的图文件")
        media = "image/svg+xml" if target.suffix.lower() == ".svg" else None
        return FileResponse(target, media_type=media, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# 内部辅助（供路由与测试使用）
# ---------------------------------------------------------------------------


def _skill_manager(settings: Settings) -> Any:
    try:
        from sciforge.skills_manager import SkillManager

        return SkillManager(settings)
    except Exception:  # noqa: BLE001
        return None


def _install_skill(settings: Settings, name: str, target: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    manager = _skill_manager(settings)
    if manager is None:
        return {
            "ok": False,
            "available": False,
            "error": "SkillManager（T4，sciforge.skills_manager）未就绪，无法安装；请先完成 T4 或手动复制 SKILL.md",
            "name": name,
            "target": target,
        }
    try:
        path = manager.install_to(name, target)
        return {
            "ok": True,
            "available": True,
            "name": name,
            "target": target,
            "path": str(path),
            "message": f"已安装到 {target}：{path}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "available": True,
            "name": name,
            "target": target,
            "error": f"安装失败：{type(exc).__name__}: {exc}",
        }


def _store_history(app: FastAPI, session_id: str, result: Any) -> None:
    """只保留干净的 user/assistant 文本，避免把 tool_calls/tool 消息回灌给下一次对话。"""
    messages = []
    for message in getattr(result, "messages", []) or []:
        role = getattr(message, "role", "")
        if role not in ("user", "assistant"):
            continue
        if getattr(message, "tool_calls", None):
            continue
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        messages.append(message)
    cutoff = time.time() - 6 * 3600
    with app.state.lock:
        app.state.sessions[session_id] = {
            "messages": messages[-12:],
            "updated": time.time(),
        }
        stale = [sid for sid, item in app.state.sessions.items() if float(item.get("updated", 0)) < cutoff]
        for sid in stale:
            app.state.sessions.pop(sid, None)


def _rel_artifacts(project_dir: Path, artifacts: Any) -> list[str]:
    results = []
    for artifact in artifacts or []:
        rel = _rel_posix(project_dir, artifact)
        if rel:
            results.append(rel)
    return results


def _scan_files(project_dir: Path, run_dir: Path) -> list[str]:
    """列出 run 目录内所有文件（相对项目根），用于前端展示与下载。"""
    results: list[str] = []
    try:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in _ARTIFACT_EXTS:
                rel = _rel_posix(project_dir, path)
                if rel:
                    results.append(rel)
    except OSError:
        pass
    return results


_GPU_KEY_CANDIDATES = {
    "index": ("index", "id", "gpu", "gpu_index"),
    "name": ("name", "model", "gpu_name", "product"),
    "memory_used": ("memory_used", "mem_used", "used_memory", "memory_used_mb", "used_mib"),
    "memory_total": ("memory_total", "mem_total", "total_memory", "memory_total_mb", "total_mib"),
    "utilization": ("utilization", "gpu_util", "util", "gpu_utilization"),
    "memory_utilization": ("memory_utilization", "mem_util", "memory_util"),
    "processes": ("processes", "procs", "users"),
}


def _normalize_gpus(data: Any) -> list[dict[str, Any]]:
    """把不同实现（T2 gpu_status / nvidia-smi 解析）的 GPU 数据结构归一成表格行。"""
    rows: list[Any] = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("gpus", "rows", "gpu_rows", "devices"):
            value = data.get(key)
            if isinstance(value, list):
                rows = value
                break
        if not rows and isinstance(data.get("servers"), list):
            for server in data["servers"]:
                if isinstance(server, dict):
                    gpus = server.get("gpus") or server.get("devices") or []
                    if isinstance(gpus, list):
                        for gpu in gpus:
                            if isinstance(gpu, dict):
                                merged = dict(gpu)
                                merged.setdefault("server", server.get("name") or server.get("server"))
                                rows.append(merged)
        if not rows and isinstance(data.get("server"), dict):
            nested = data["server"].get("gpus") if isinstance(data["server"], dict) else None
            if isinstance(nested, list):
                rows = nested

    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item: dict[str, Any] = {}
        for key, candidates in _GPU_KEY_CANDIDATES.items():
            item[key] = None
            for candidate in candidates:
                if candidate in row and row[candidate] is not None:
                    item[key] = row[candidate]
                    break
        for extra in ("server", "driver", "temperature", "power"):
            if extra in row:
                item[extra] = row[extra]
        if any(value is not None for key, value in item.items() if key != "index"):
            normalized.append(item)
    return normalized


__all__ = ["create_app", "STATIC_DIR", "FIXTURES_DIR"]
