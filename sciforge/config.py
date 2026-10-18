"""分层配置：默认值 ← ~/.sciforge/config.toml ← <project>/.sciforge/project.toml。

所有模块统一通过 :func:`get_settings` 读取配置，不要自行解析 TOML。
密钥只从配置文件或环境变量读取，永远不写入项目目录。
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

if sys.version_info >= (3, 11):  # pragma: no cover
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

SCIFORGE_HOME_ENV = "SCIFORGE_HOME"
PROJECT_DIR_NAME = ".sciforge"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


def data_home() -> Path:
    """`~/.sciforge`；可用 ``SCIFORGE_HOME`` 覆盖（测试/多环境隔离）。"""
    env = os.environ.get(SCIFORGE_HOME_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".sciforge"


def global_config_path() -> Path:
    return data_home() / "config.toml"


def project_config_path(project_dir: str | Path) -> Path:
    return Path(project_dir).expanduser().resolve() / PROJECT_DIR_NAME / "project.toml"


def project_runtime_dir(project_dir: str | Path) -> Path:
    return Path(project_dir).expanduser().resolve() / PROJECT_DIR_NAME


def find_project_root(start: str | Path | None = None) -> Path | None:
    """从 start 向上寻找包含 .sciforge/project.toml 的目录。"""
    current = Path(start or Path.cwd()).expanduser().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / PROJECT_DIR_NAME / "project.toml").exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ProviderConfig(_Model):
    """一个 OpenAI 兼容 LLM 服务。"""

    type: str = "openai-compatible"
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    api_key: str | None = None
    api_key_env: str | None = "DEEPSEEK_API_KEY"
    default_model: str = "deepseek-chat"
    models: list[str] = Field(default_factory=lambda: ["deepseek-chat", "deepseek-reasoner"])
    timeout: float = 180.0
    temperature: float = 0.6
    max_tokens: int = 4096
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # 美元 / 百万 token，可选；用于成本估算
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    # 有些服务（如 deepseek-reasoner）不支持 temperature / tools，交给 provider 自行忽略
    supports_tools: bool = True
    supports_stream: bool = True


class RoleConfig(_Model):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class LLMConfig(_Model):
    default: str = "deepseek"
    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "deepseek": ProviderConfig(
                base_url=DEFAULT_DEEPSEEK_BASE_URL,
                api_key_env="DEEPSEEK_API_KEY",
                default_model="deepseek-chat",
                models=["deepseek-chat", "deepseek-reasoner"],
                input_cost_per_mtok=0.27,
                output_cost_per_mtok=1.10,
            )
        }
    )
    # 科研流水线中不同角色可路由到不同模型
    roles: dict[str, RoleConfig] = Field(
        default_factory=lambda: {
            "planner": RoleConfig(provider="deepseek", model="deepseek-reasoner"),
            "executor": RoleConfig(provider="deepseek", model="deepseek-chat"),
            "writer": RoleConfig(provider="deepseek", model="deepseek-chat"),
            "reviewer": RoleConfig(provider="deepseek", model="deepseek-reasoner"),
            "vision": RoleConfig(provider="deepseek", model="deepseek-chat"),
        }
    )

    def provider_name_for(self, role: str) -> str:
        rc = self.roles.get(role)
        if rc and rc.provider:
            return rc.provider
        return self.default


class LiteratureConfig(_Model):
    email: str | None = None  # Unpaywall / 礼貌池 user-agent 需要
    s2_api_key_env: str = "S2_API_KEY"
    s2_api_key: str | None = None  # 可选：GUI 写入本机配置；env 优先
    unpaywall_email: str | None = None
    daily_arxiv_language: str = "Chinese"  # Chinese | English
    arxiv_categories: list[str] = Field(
        default_factory=lambda: ["cs.CV", "cs.CL", "cs.LG", "cs.AI"]
    )
    request_timeout: float = 30.0
    cache_ttl_seconds: int = 3600
    user_agent: str = "SciForge/0.1 (+https://github.com/sciforge/sciforge)"


class FigureConfig(_Model):
    style: str = "neurips"  # neurips | cvpr | iclr | ieee
    palette: str = "okabe-ito"  # okabe-ito | tableau | nature
    font: str = "DejaVu Sans"
    dpi: int = 300
    formats: list[str] = Field(default_factory=lambda: ["pdf", "svg", "png"])
    width_inches: float = 3.25  # 单栏
    height_inches: float = 2.2


class WritingConfig(_Model):
    venue: str = "cvpr"  # cvpr | neurips | iclr | acl | generic
    language: str = "zh"  # 论文草稿语言; en 为正式英文
    latex_engine: str = "xelatex"
    latex_template: str | None = None
    citation_style: str = "natbib"


class ServerConfig(_Model):
    """一台 GPU 服务器（SSH）。"""

    name: str
    host: str
    port: int = 22
    user: str = "root"
    identity_file: str | None = None
    password_env: str | None = None  # 需要 sshpass 时的环境变量名
    proxy_jump: str | None = None  # 跳板：user@host
    workdir: str = "~/sciforge"
    conda_env: str | None = None
    python: str = "python"
    tmux_session: str = "sciforge"
    scheduler: str = "none"  # none | slurm
    partition: str | None = None
    gpu_type: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    sync_excludes: list[str] = Field(
        default_factory=lambda: ["data/", "outputs/", "wandb/", "__pycache__/", ".venv/"]
    )
    max_parallel_jobs: int = 2
    # 首次连接时是否自动创建 workdir
    auto_mkdir: bool = True


class SkillsConfig(_Model):
    extra_dirs: list[str] = Field(default_factory=list)
    targets: dict[str, str] = Field(
        default_factory=lambda: {
            "dsh": "~/.dsh/skills",
            "claude": "~/.claude/skills",
            "codex": "~/.codex/skills",
        }
    )
    auto_install_dsh: bool = False


class WebConfig(_Model):
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = False


class AgentConfig(_Model):
    max_steps: int = 30
    context_token_budget: int = 96000
    compact_keep_messages: int = 12
    approval: str = "auto"  # auto | ask | deny
    max_tool_output_chars: int = 12000
    temperature: float = 0.3
    language: str = "zh"


class PipelineConfig(_Model):
    default_recipe: str = "full"
    stop_on_stage_error: bool = False
    save_artifacts: bool = True


class Settings(_Model):
    project_name: str = ""
    project_dir: Path | None = None
    llm: LLMConfig = Field(default_factory=LLMConfig)
    literature: LiteratureConfig = Field(default_factory=LiteratureConfig)
    figures: FigureConfig = Field(default_factory=FigureConfig)
    writing: WritingConfig = Field(default_factory=WritingConfig)
    servers: list[ServerConfig] = Field(default_factory=list)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)

    # ---- 便捷方法 ----
    def server(self, name: str | None = None) -> ServerConfig | None:
        if not self.servers:
            return None
        if not name:
            return self.servers[0]
        for server in self.servers:
            if server.name == name:
                return server
        return None

    def resolved_provider(self, name: str | None = None) -> ProviderConfig:
        key = name or self.llm.default
        provider = self.llm.providers.get(key)
        if provider is None:
            raise ConfigError(f"未配置 LLM provider: {key}（可用: {', '.join(self.llm.providers) or '无'}）")
        return provider

    def resolve_api_key(self, name: str | None = None) -> str | None:
        provider = self.resolved_provider(name)
        if provider.api_key:
            return provider.api_key
        if provider.api_key_env:
            value = os.environ.get(provider.api_key_env)
            if value:
                return value
        # DeepSeek /provider 名的常见回退
        key = name or self.llm.default
        for env_name in (f"{key.upper()}_API_KEY", "OPENAI_API_KEY"):
            value = os.environ.get(env_name)
            if value:
                return value
        return None


class ConfigError(RuntimeError):
    """配置错误（可读、可指导用户修复）。"""


# ---------------------------------------------------------------------------
# 读写与合并
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"配置文件解析失败: {path}\n{exc}") from exc


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def get_settings(project_dir: str | Path | None = None) -> Settings:
    """加载合并后的配置。

    project_dir 为空时自动向上查找项目根；找不到则只用全局配置。
    """
    merged: dict[str, Any] = {}

    global_path = global_config_path()
    if global_path.exists():
        merged = _deep_merge(merged, _read_toml(global_path))

    project_root = Path(project_dir).expanduser().resolve() if project_dir else find_project_root()
    project_cfg: dict[str, Any] = {}
    if project_root is not None:
        project_cfg = _read_toml(project_root / PROJECT_DIR_NAME / "project.toml")
        merged = _deep_merge(merged, project_cfg)

    try:
        settings = Settings.model_validate(merged)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"配置校验失败，请检查 {global_path} 或项目配置：\n{exc}") from exc

    settings.project_dir = project_root
    if not settings.project_name:
        if project_cfg.get("project_name"):
            settings.project_name = str(project_cfg["project_name"])
        elif project_root is not None:
            settings.project_name = project_root.name
    return settings


def _strip_none(value: Any) -> Any:
    """TOML 没有 null：递归剔除 None，避免写回后无法解析。"""
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value if v is not None]
    return value


def dump_toml(data: dict[str, Any]) -> str:
    """把 dict 渲染为合法 TOML（优先 tomli-w，缺省时用内置简易序列化）。"""
    cleaned = _strip_none(data)
    try:
        import tomli_w

        return tomli_w.dumps(cleaned)
    except ImportError:  # pragma: no cover - tomli-w 是依赖，仅为极端环境兜底
        pass

    lines: list[str] = []

    def _fmt(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, list):
            return "[" + ", ".join(_fmt(v) for v in value) + "]"
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _walk(prefix: str, obj: dict[str, Any]) -> None:
        plain = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        nested = {k: v for k, v in obj.items() if isinstance(v, dict)}
        table_list = {k: v for k, v in obj.items() if isinstance(v, list) and v and isinstance(v[0], dict)}
        if prefix:
            lines.append(f"\n[{prefix}]")
        for key, value in plain.items():
            lines.append(f"{key} = {_fmt(value)}")
        for key, value in table_list.items():
            for item in value:
                lines.append(f"\n[[{prefix + '.' if prefix else ''}{key}]]")
                for sub_key, sub_value in item.items():
                    if not isinstance(sub_value, dict):
                        lines.append(f"{sub_key} = {_fmt(sub_value)}")
        for key, value in nested.items():
            _walk(f"{prefix}.{key}" if prefix else key, value)

    _walk("", cleaned)
    return "\n".join(lines).strip() + "\n"


def save_global_config(settings: Settings) -> Path:
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json", exclude={"project_dir", "project_name"})
    header = "# SciForge 全局配置（自动生成，可手工编辑）\n# 密钥推荐使用环境变量：DEEPSEEK_API_KEY / S2_API_KEY / OPENAI_API_KEY\n\n"
    path.write_text(header + dump_toml(data), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def save_project_config(project_dir: Path, settings: Settings) -> Path:
    path = project_config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json", exclude={"project_dir"})
    # 项目配置只保存项目级字段，避免把密钥/全局设置复制进仓库
    project_data = {
        "project_name": data.get("project_name", ""),
        "writing": data.get("writing", {}),
        "figures": data.get("figures", {}),
    }
    path.write_text(dump_toml(project_data), encoding="utf-8")
    return path


def ensure_dirs(settings: Settings | None = None) -> Path:
    home = data_home()
    for sub in ("library", "skills", "cache", "logs", "papers", "sessions"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return home


DEFAULT_CONFIG_TOML = """\
# SciForge 全局配置（~/.sciforge/config.toml）
# 密钥推荐用环境变量：DEEPSEEK_API_KEY / S2_API_KEY / OPENAI_API_KEY

[llm]
default = "deepseek"

[llm.providers.deepseek]
type = "openai-compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
default_model = "deepseek-chat"
models = ["deepseek-chat", "deepseek-reasoner"]
input_cost_per_mtok = 0.27
output_cost_per_mtok = 1.10

# 任意 OpenAI 兼容服务（vLLM / Ollama / OpenRouter / 硅基流动 / 公司内网）
# [llm.providers.local]
# type = "openai-compatible"
# base_url = "http://127.0.0.1:8000/v1"
# api_key_env = "LOCAL_API_KEY"
# default_model = "Qwen/Qwen2.5-72B-Instruct"

[llm.roles.planner]
provider = "deepseek"
model = "deepseek-reasoner"

[llm.roles.executor]
provider = "deepseek"
model = "deepseek-chat"

[literature]
# email = "you@example.com"   # Unpaywall 礼貌池
# s2_api_key_env = "S2_API_KEY"

[figures]
style = "neurips"
palette = "okabe-ito"

[writing]
venue = "cvpr"
latex_engine = "xelatex"

# [[servers]]
# name = "gpu1"
# host = "10.0.0.2"
# port = 22
# user = "root"
# identity_file = "~/.ssh/id_ed25519"
# workdir = "~/sciforge"
# conda_env = "torch"
# tmux_session = "sciforge"
# scheduler = "slurm"        # 可选
# partition = "gpu"
"""


__all__ = [
    "SCIFORGE_HOME_ENV",
    "DEFAULT_DEEPSEEK_BASE_URL",
    "data_home",
    "global_config_path",
    "project_config_path",
    "project_runtime_dir",
    "find_project_root",
    "ProviderConfig",
    "RoleConfig",
    "LLMConfig",
    "LiteratureConfig",
    "FigureConfig",
    "WritingConfig",
    "ServerConfig",
    "SkillsConfig",
    "WebConfig",
    "AgentConfig",
    "PipelineConfig",
    "Settings",
    "ConfigError",
    "get_settings",
    "save_global_config",
    "save_project_config",
    "ensure_dirs",
    "dump_toml",
    "DEFAULT_CONFIG_TOML",
]
