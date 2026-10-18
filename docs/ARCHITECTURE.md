# SciForge 架构与协作契约（v0.1）

> 本文件是并行开发的**唯一接口契约**。任何模块都不得修改本文件定义的签名；
> 如需扩展，先在本文件追加小节并在交付说明中标注。

## 1. 产品定位

SciForge 是一个**本地运行、DeepSeek 驱动、可接任意 OpenAI 兼容 LLM、可 SSH 调度 GPU 服务器**
的深度学习科研智能体 Harness。它把科研流程拆成可排列组合的能力模块（Skill + Tool + Pipeline），
覆盖：

```
选题/文献 → 读论文 → 找创新点 → 实验设计 → 写代码 → 远程训练/实验 → 结果分析
→ 论文写作 → 顶刊/顶会级模型图与实验图 → 审稿式自检 → 投稿材料
```

三个使用入口，共享同一套核心：

1. `sciforge` CLI（Typer）：确定性工具 + 交互式 agent + pipeline。
2. `sciforge serve` 本地 Web 控制台（FastAPI + 静态页）：会话、文献、服务器、实验、图表。
3. **DSH 集成**：`sciforge skills install --target dsh` 把 Harness 的 Skill 装进
   `~/.dsh/skills/`，让 DeepSeek Harness（DSH）直接驱动本 Harness 的 CLI/工具。

## 2. 仓库布局与归属（write scope）

```
pyproject.toml, .gitignore                  Lead
README.md / docs/*.md                       Lead
sciforge/__init__.py                        Lead
sciforge/cli.py                             Lead（各模块用 register(app) 挂子命令）
sciforge/config.py                          Lead
sciforge/core/                              Lead
sciforge/llm/                               Lead
sciforge/agent/                             Lead
sciforge/pipeline/                          Lead
sciforge/ideation.py                        Lead
sciforge/tools/__init__.py  (自动 import 各模块)   Lead
sciforge/tools/literature.py                T1 literature
sciforge/tools/paper_reader.py              T1 literature
sciforge/library.py                         T1 literature
sciforge/tools/remote.py                    T2 remote
sciforge/tools/experiments.py               T2 remote
sciforge/templates/experiments/**           T2 remote
sciforge/figures/**                         T3 figures-writing
sciforge/writing/**                         T3 figures-writing
sciforge/templates/paper/**                 T3 figures-writing
sciforge/templates/figures/**               T3 figures-writing
sciforge/skills_manager.py                  T4 skills
sciforge/skills_lib/**                      T4 skills
sciforge/catalog/**                         T4 skills
sciforge/tools/skills.py                   T4 skills（add_cli(app) 挂 `sciforge skills ...`）
sciforge/web/**                             T5 web
examples/demo/**                            T5 web（fixtures + demo 脚本；Lead 负责 demo 入口串起来）
tests/test_<module>.py                      各自模块作者
```

规则：

- **只写自己 scope 内的文件**；需要改别人的接口时，先给 Lead 发消息，不要直接改。
- 所有 `__init__.py` 由 Lead 预创建；子模块通过 `register_tool(...)` / `register(app)` 自注册。
- 不要新增重量级依赖；需要的新依赖先告知 Lead，由 Lead 写进 `pyproject.toml`。
- 所有中文文案用 UTC+8 语境；代码标识符英文；docstring 可中英混排。

## 3. 核心数据结构（`sciforge/core/types.py`，已实现，不可改签名）

```python
@dataclass
class ToolResult:
    ok: bool
    content: str                      # 回给 LLM 的文本（简洁、可读、含关键数字）
    data: dict[str, Any] = field(default_factory=dict)   # 结构化结果
    artifacts: list[str] = field(default_factory=list)   # 产出文件绝对路径
    error: str | None = None
    display: str | None = None        # CLI/Web 展示用富文本（可选）

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class ChatMessage:
    role: str                         # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    def to_api_dict(self) -> dict: ...

@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    usage: Usage
    finish_reason: str = "stop"
    model: str = ""
    raw: dict[str, Any] | None = None
```

## 4. Tool 契约（`sciforge/core/registry.py`）

```python
class Tool(Protocol):
    name: str                # snake_case，全局唯一
    description: str         # 给模型看，说明何时用、返回什么
    parameters: dict         # JSON Schema（type=object）
    tags: tuple[str, ...]    # 分类: "literature" | "paper" | "remote" | "experiment" | "figure" | "writing" | "fs" | "skill"
    requires: tuple[str, ...]  # 环境要求，如 ("ssh",) ；doctor 会检查
    def run(self, ctx: "ToolContext", **kwargs) -> ToolResult: ...

def register_tool(tool: Tool) -> Tool: ...

@dataclass
class ToolContext:
    settings: "Settings"          # sciforge.config.Settings
    project_dir: Path             # 当前科研项目工作目录
    data_dir: Path                # ~/.sciforge
    session_dir: Path             # <project>/.sciforge/sessions/<session_id>
    emit: Callable[[str, dict], None]   # 事件回调（CLI/Web 实时展示）；emit("log", {...})
    approve: Callable[[str, dict], bool]  # 审批回调；本地/自动模式下恒 True
    extra: dict[str, Any]         # 会话级共享状态（如已加载的 Skill、LLM router）
    def log(self, msg: str, level: str = "info") -> None: ...
    def artifact(self, name: str) -> Path: ...   # session_dir / name
```

- 工具实现放在 `sciforge/tools/<module>.py`，模块内用 `register_tool(...)` 自注册。
- 工具**不得**直接 `print`；通过 `ctx.log` / `ToolResult.content` 输出。
- 工具应优雅降级：网络失败 → `ToolResult(ok=False, error=...)`，不抛未捕获异常。
- 长耗时工具（远程训练）要返回 job id 和查询命令，不要阻塞等待。
- 测试里不得访问真实网络/真实 GPU 服务器；用 `respx`/monkeypatch/本地临时目录。
  真实网络测试必须标 `@pytest.mark.integration`，默认 `-m "not integration"`。

## 5. LLM 契约（`sciforge/llm/base.py`）

```python
class LLMProvider(ABC):
    name: str
    def chat(self, messages: list[ChatMessage], *, tools: list[dict] | None = None,
             model: str | None = None, temperature: float | None = None,
             max_tokens: int | None = None, stream: bool = False,
             on_delta: Callable[[str], None] | None = None) -> LLMResponse: ...

class LLMRouter:
    def for_role(self, role: str) -> LLMProvider          # planner|executor|writer|reviewer|vision
    def chat(self, role: str, messages, **kw) -> LLMResponse
    def complete(self, role: str, prompt: str, system: str | None = None, **kw) -> str
    usage: Usage                                          # 累计
```

- 默认 provider：`deepseek`，`base_url=https://api.deepseek.com/v1`，`deepseek-flash` / `deepseek-v4-pro`；
  api key 从 `DEEPSEEK_API_KEY`（或配置里的 `api_key_env` / `api_key`）读取。
- 任意 OpenAI 兼容服务（OpenAI / OpenRouter / vLLM / Ollama / 硅基流动 …）通过配置
  `[[llm.providers.<name>]] type="openai-compatible"` 添加。
- `MockProvider`（`sciforge/llm/mock.py`）用于离线 demo/测试：按 `ScriptedResponse` 或启发式回复，
  也会在需要时发起脚本化 tool call。

## 6. 配置契约（`sciforge/config.py`）

- 全局：`~/.sciforge/config.toml`（`SCIFORGE_HOME` 可覆盖 `~/.sciforge`）。
- 项目：`<project>/.sciforge/project.toml`，覆盖全局。
- 结构（Pydantic 模型，字段可增，不可改已用字段名）：

```toml
[llm]
default = "deepseek"
[llm.providers.deepseek]
type = "openai-compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
default_model = "deepseek-flash"
models = ["deepseek-flash", "deepseek-v4-pro"]
[llm.roles.planner]
provider = "deepseek"
model = "deepseek-v4-pro"

[literature]
email = ""            # Unpaywall / 礼貌池
# Semantic Scholar 已按需求停用（适配器保留，不默认暴露）
[figures]
style = "neurips"
palette = "okabe-ito"
[writing]
venue = "cvpr"
latex_engine = "xelatex"

[[servers]]
name = "gpu1"
host = "10.0.0.2"
port = 22
user = "root"
identity_file = "~/.ssh/id_ed25519"
workdir = "~/sciforge"
conda_env = "torch"
tmux_session = "sciforge"
```

- 代码里通过 `get_settings(project_dir: Path | None = None) -> Settings` 取，**不要**自己解析 TOML。
- 密钥永远不写入项目目录；只允许环境变量或 `~/.sciforge/config.toml`。

## 7. 事件契约（`sciforge/core/events.py`）

```python
bus = EventBus()                       # 线程安全
bus.subscribe(handler)                 # handler(event: Event) -> None
bus.emit(kind: str, payload: dict)     # kind: "log" | "tool_call" | "tool_result"
                                       #       | "llm_delta" | "stage" | "artifact" | "error"
```

CLI 用 Rich 渲染；Web 用 SSE 转发；两者都只消费 Event。

## 8. Pipeline 契约（`sciforge/pipeline/engine.py`）

```python
@dataclass
class Stage:
    name: str; title: str; prompt: str
    tools: list[str] = field(default_factory=list)   # 空 = 全部
    role: str = "executor"
    artifacts: list[str] = ...                        # 期望产出文件名，如 ["survey.md"]
    max_steps: int = 12

@dataclass
class Recipe:
    name: str; title: str; description: str
    stages: list[Stage]
    tags: tuple[str, ...] = ()

class PipelineRunner:
    def __init__(self, settings, project_dir, router=None, mock=False)
    def run(self, recipe: Recipe | str, topic: str, until: str | None = None) -> PipelineRun
```

- 内置 recipe 在 `sciforge/pipeline/recipes.py`（Lead 实现）。
- 每个 stage 产出 `<project>/.sciforge/runs/<run_id>/<stage>.md`，并调用 LLM 做结构化总结。
- `sciforge pipeline run --recipe full --topic "..." --mock` 必须能在**无 API key、无网络**下跑通
  （MockProvider + demo fixtures）。

## 9. CLI 契约

- 主入口 `sciforge = sciforge.cli:main`。
- 子模块用 `def register(app: typer.Typer) -> None` 挂子命令，Lead 在 `cli.py` 中调用。
- 命令一律支持 `--json` 输出（机器可读）；错误用红色面板，不裸抛 traceback（`--debug` 除外）。
- 示例：

```
sciforge init my-project --topic "..."
sciforge doctor
sciforge llm test --role planner
sciforge chat "帮我调研扩散模型在视频生成中的最新进展"
sciforge lit search "diffusion transformer" --source arxiv --limit 20
sciforge lit daily --category cs.CV
sciforge read 2401.00001 --note
sciforge idea generate "<topic>"
sciforge ssh add gpu1 --host 1.2.3.4 --user root --key ~/.ssh/id_ed25519
sciforge ssh test gpu1
sciforge gpu status gpu1
sciforge exp scaffold --template cv-classification --name my-exp
sciforge exp launch --server gpu1 --script train.py
sciforge exp status / logs / results
sciforge fig arch --spec arch.yaml -o figures/arch.svg
sciforge fig plot --spec curves.yaml -o figures/curves.pdf
sciforge write draft --venue cvpr
sciforge skills list | search | install | lint
sciforge pipeline run --recipe full --topic "..."
sciforge serve --port 8765
```

## 10. Skill 契约（`sciforge/skills_lib/`）

- 每个 skill 一个目录：`skills_lib/<name>/SKILL.md`，可选 `references/`、`scripts/`、`assets/`。
- YAML frontmatter 必填：`name`（=目录名）、`description`（含触发条件与产出）、`version`、`license`；
  建议 `metadata: {author, tags}`、`compatibility: "sciforge>=0.1"`。
- 参照 Awesome-Scientific-Skills / skill-metric 评分规范：正文 < 5k 字符，详细内容放 references/，
  必须含示例与失败处理，必须引用 references/scripts。
- 与 DSH 的 `~/.dsh/skills/<name>/SKILL.md` 格式兼容（frontmatter + 正文）。
- 技能分类（tags）：`literature` `reading` `ideation` `experiment` `remote` `writing` `figure`
  `review` `venue` `reproducibility` `meta`。

## 11. 图表契约（`sciforge/figures/`）

```python
# sciforge/figures/arch.py
def render_architecture(spec: dict, out: Path, fmt: str | None = None) -> Path
# spec: {"title":..., "width": 1200, "nodes":[{id,label,sub,kind,group,row,col}], "edges":[{from,to,label,style}], "groups":[...]}
# kind: "tensor" | "module" | "op" | "loss" | "data" | "note"
# fmt: svg | pdf | png（png 无 cairosvg 时优雅降级到 svg）

# sciforge/figures/plots.py
def render_plot(spec: dict, out: Path) -> Path
# spec: {"kind": "line|bar|scatter|radar|heatmap", "series": [...], "style": "neurips", "xlabel":..., ...}

# sciforge/figures/tables.py
def render_latex_table(spec: dict) -> str     # booktabs 三线表
```

- 默认风格接近 CVPR/NeurIPS/ICLR 论文：无顶右边框、色盲友好配色（Okabe-Ito）、正交字号（7/8/9pt）、
  导出矢量、`bbox_inches="tight"`、PDF 字体嵌入。
- 所有图必须可复现：spec 落盘（YAML/JSON）与最终文件同目录。

## 12. 测试与验收

- 每个模块交付时：`uv run pytest tests/test_<module>.py -q` 全绿，ruff 无 error。
- Lead 会在合并后跑全量：`uv run pytest -q`、`sciforge doctor`、`sciforge demo`。
- 不要留下未使用的 import、TODO 占位实现、`raise NotImplementedError`。
- 交付说明写在最终消息里：改了哪些文件、如何验证、已知限制。

## 13. 环境

- 工作目录：`/Users/sddzlsc/Documents/北京万维速达/sci`（所有命令在此执行）。
- Python：`.venv`（uv 创建，3.12），用 `uv pip install -e ".[dev]"`；运行用 `.venv/bin/python`。
- 不得启动常驻服务占用 8765 端口以外端口；不得修改 `~/.dsh` 之外的全局配置。

---

## 14. 跨模块 API（并行开发必须遵守）

### 14.1 `sciforge/library.py`（T1 实现，T5 消费）

```python
@dataclass
class PaperRecord:
    id: str                      # arXiv ID / DOI / S2 ID
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    url: str = ""
    pdf_url: str = ""
    published: str = ""
    venue: str = ""
    categories: list[str] = field(default_factory=list)
    summary: str = ""            # 中文/英文摘要（daily-arxiv 等）
    tags: list[str] = field(default_factory=list)
    local_pdf: str = ""          # 绝对路径
    notes_path: str = ""         # 绝对路径
    source: str = "arxiv"        # arxiv|s2|openalex|hf|daily|manual
    added_at: str = ""           # ISO8601

class PaperLibrary:
    def __init__(self, data_dir: Path) -> None: ...   # 数据在 <data_dir>/library/
    def add(self, record: PaperRecord) -> PaperRecord: ...
    def get(self, paper_id: str) -> PaperRecord | None: ...
    def list(self, *, limit: int = 50, tag: str | None = None) -> list[PaperRecord]: ...
    def search(self, query: str, *, limit: int = 20) -> list[PaperRecord]: ...
    def save_pdf(self, paper_id: str, content: bytes) -> Path: ...
    def save_note(self, paper_id: str, markdown: str) -> Path: ...
    def note(self, paper_id: str) -> str | None: ...
    def update(self, record: PaperRecord) -> None: ...
```

### 14.2 `sciforge/skills_manager.py`（T4 实现，T5/CLI/Pipeline 消费）

```python
@dataclass
class SkillMeta:
    name: str
    description: str
    path: Path                   # SKILL.md 路径
    tags: list[str] = field(default_factory=list)
    version: str = ""
    source: str = "bundled"      # bundled|installed|external|user
    dir: Path | None = None

class SkillManager:
    def __init__(self, settings: Settings | None = None) -> None: ...
    def list(self) -> list[SkillMeta]: ...
    def get(self, name: str) -> SkillMeta | None: ...
    def load(self, name: str) -> str: ...          # 完整正文（含 frontmatter）
    def search(self, query: str, *, limit: int = 10) -> list[SkillMeta]: ...
    def install(self, source: str, *, target: str | None = None, name: str | None = None) -> list[Path]: ...
    def install_to(self, name: str, target: str) -> Path: ...   # "dsh" | "claude" | "project"
    def lint(self, name: str) -> dict: ...          # {"ok":bool,"score":int,"issues":[...]}
```

### 14.3 `sciforge/figures/*`（T3）与 `sciforge/writing/*`（T3）

- `figures/arch.py: render_architecture(spec: dict, out: Path, *, fmt=None) -> Path`
- `figures/plots.py: render_plot(spec: dict, out: Path, *, style=None) -> Path`
- `figures/tables.py: render_latex_table(spec: dict) -> str`
- `figures/styles/`：`.mplstyle` 与 palette 定义
- `writing/paper.py`：
  - `create_paper_project(dest: Path, *, venue: str, title: str, authors: list[str]) -> Path`
  - `PaperProject(dest)`：`add_section(name, content)`, `compile() -> Path | None`, `check() -> list[dict]`
  - `writing/citations.py`：`add_bibtex(entries: str)`, `collect_from_arxiv(ids) -> str`；写作工具名为 `citation_add`（`paper_cite` 归 T1 文献模块）
  - `writing/review.py`：`self_review(paper_dir) -> str`（可选 LLM）
- `writing/templates` 在 `sciforge/templates/paper/`，Jinja2 渲染。

### 14.4 `sciforge/web`（T5）

- `sciforge/web/app.py: create_app(settings: Settings | None = None) -> FastAPI`
- `sciforge/web/server.py: run_server(host: str | None = None, port: int | None = None, settings=None) -> None`
- API 约定（必须实现）：
  - `GET /api/health` → `{ok, version, mock, llm, tools, servers}`
  - `GET /api/tools` → 工具列表（含 tags）
  - `POST /api/chat` → SSE（`text/event-stream`），事件复用 EventBus：`log|assistant|tool_call|tool_result|llm_delta|stage|error|done`
  - `GET /api/library?query=` → 文献列表（library 缺失时返回空并标 `unavailable`）
  - `GET /api/skills` → 技能列表
  - `GET /api/servers` → 服务器配置（不含密钥）+ 可选实时 GPU 状态
  - `GET /api/runs` → pipeline 运行历史（读 `<project>/.sciforge/runs/*/run.json`）
  - `POST /api/pipeline/run` → 启动（后台线程）并返回 run_id；配合 `GET /api/pipeline/{run_id}` 轮询
  - `GET /figures/{path}` / `GET /files/{path}`：安全地暴露项目内产物
- 静态页面：`sciforge/web/static/index.html`（无构建步骤，vanilla JS/CSS）。
- 配置类端点（GUI 填空配置，写入 `~/.sciforge/config.toml`，密钥不回传浏览器）：
  `GET/POST /api/llm`、`POST/DELETE /api/llm/providers`、`POST /api/llm/roles`、`POST /api/llm/test`；
  `POST /api/servers`、`DELETE /api/servers/{name}`、`POST /api/servers/{name}/test`；
  `GET/POST /api/literature/config`；
  `POST /api/upload`（对话附件）、`POST /api/servers/{name}/upload`（上传到 GPU）；
  `GET /api/download/{path}`（浏览器下载项目文件）、`GET /api/servers/{name}/metrics`（GPU 实时指标）。
- 实现可额外提供前端所需端点（不属于契约、不破坏上述约定）：`/api/recipes`、
  `/api/literature/search`、`/api/library/add`、`/api/paper/read`、`/api/gpu`、
  `/api/artifacts`、`POST /api/skills/install-dsh`、`POST /api/skills/install`、
  `GET /api/docs`（OpenAPI）。
- 所有依赖其它模块的 import 必须放在函数内部（延迟导入 + try/except），保证模块未完成时
  Web 仍能启动并显示明确提示。

### 14.5 demo fixtures（T5）

`examples/demo/fixtures/`：
- `arxiv_search.json`：模拟 arXiv 检索结果（≥6 篇，含 id/title/authors/summary/pdf）
- `daily_arxiv.jsonl`：3 行 daily-arXiv-ai-enhanced 格式数据
- `paper.txt`：一篇可用 pypdf/pymupdf 生成的示例论文全文文本（或 `paper.pdf`）
- `arch_spec.yaml`：Figure 1 模型图 DSL
- `plot_spec.yaml`：实验结果曲线 spec
- `results/metrics.json`：实验结果（用于写作/制表）
- `README.md`：fixtures 说明

### 14.6 CLI 子命令挂载（各模块提供 `register(app)`）

- T1：`sciforge lit ...`、`sciforge read ...`
- T2：`sciforge ssh ...`、`sciforge gpu ...`、`sciforge exp ...`
- T3：`sciforge fig ...`、`sciforge write ...`
- T4：`sciforge skills ...`
- T5：不挂 CLI；由 Lead 的 `sciforge serve` 调用。
