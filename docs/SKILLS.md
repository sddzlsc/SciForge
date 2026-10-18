# SciForge Skills —— 科研 Skill 体系与外部生态

> Skill = 可复用的科研工作流说明（何时用、怎么做、怎么检查、失败怎么办），
> 由智能体按需加载，遵循 Anthropic Agent Skills / skill-metric 规范，与 DSH、Claude、Codex 兼容。

## 1. 内置技能清单（24 个）

| # | skill | tags | 一句话用途 | 主要工具 |
|---|---|---|---|---|
| 1 | `sciforge-harness` | meta | **DSH/Claude/Codex 宿主如何驱动 SciForge**（入口技能） | `sciforge doctor/chat/pipeline` |
| 2 | `literature-survey` | literature | 系统文献调研、写 related work、找 gap | `lit_search` `paper_fetch` `paper_cite` |
| 3 | `paper-deep-read` | reading | 精读单篇论文、提炼方法、准备复现 | `paper_read` `paper_note` `paper_ask` |
| 4 | `daily-arxiv-digest` | literature | 每日 arXiv 速览与分级 | `lit_daily` `lit_search` |
| 5 | `paper-retrieval-oa` | literature | 找 OA 全文/预印本/机构版本 | `paper_fetch` `oa_lookup.py` |
| 6 | `research-ideation` | ideation | 结构化思维透镜生成研究方向 | `sciforge idea` `lit_search` |
| 7 | `innovation-mining` | ideation | 从 gap 挖可证伪创新点 | `lit_search` `sciforge idea` |
| 8 | `idea-novelty-check` | ideation | 新颖性终检与撞车排查 | `lit_search` `paper_compare` |
| 9 | `experiment-design` | experiment | claim→实验矩阵→预算→判据 | `exp_scaffold` `exp_launch` |
| 10 | `benchmark-eval-protocol` | experiment | 评测协议、防泄漏、公平比较 | `exp_results` `figure_table` |
| 11 | `dataset-audit` | experiment | 数据集质量/标签噪声/许可合规 | `figure_plot` `benchmark-eval-protocol` |
| 12 | `remote-gpu-training` | remote | SSH GPU 训练、tmux/Slurm、同步 | `remote_run` `gpu_status` `remote_sync` |
| 13 | `distributed-training` | experiment | DDP/FSDP/ZeRO/TP 选择与调优 | `exp_launch` `gpu_status` |
| 14 | `training-debugging` | experiment | OOM/NaN/不收敛/慢的排障 | `remote_tail` `diagnose_nan.py` |
| 15 | `autoresearch-loop` | meta | 双循环自主科研、状态文件续跑 | `pipeline` `exp_*` |
| 16 | `paper-figures-architecture` | figure | Figure 1 架构图/系统图 | `figure_architecture` |
| 17 | `paper-figures-results` | figure | 实验曲线/柱状/热力图 | `figure_plot` |
| 18 | `academic-plotting` | figure | 图型选择、出版级审美 | `figure_plot` `figure_architecture` |
| 19 | `paper-writing` | writing | 论文草稿与投稿前自检 | `paper_section` `paper_compile` |
| 20 | `ml-paper-writing` | writing | 顶会写作方法论、引用核验 | `paper_cite` `paper_review` |
| 21 | `latex-venue-submission` | venue | LaTeX 工程、模板、打包 | `paper_init` `paper_compile` |
| 22 | `reviewer-response` | review | Rebuttal/审稿意见逐条回复 | `paper_section` `exp_launch` |
| 23 | `reproducibility-package` | reproducibility | 环境锁定、一键复现、artifact | `collect_env.py` `exp_results` |
| 24 | `slide-talk` | writing | 组会/会议 slides 与讲稿 | `figure_*` `sciforge chat` |

素材参考并改写了 [Orchestra Research AI-research-SKILLs](https://github.com/orchestra-research/AI-research-SKILLs)、[K-Dense scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)、[Awesome-Scientific-Skills](https://github.com/InternScience/Awesome-Scientific-Skills) 与 Rimagination/ScanSci 生态的思路，并适配 SciForge 工具链。

## 2. 目录结构

```
sciforge/skills_lib/
└── literature-survey/
    ├── SKILL.md                 # 必有：frontmatter + 正文（<5000 字符）
    ├── references/*.md          # 可选：详细资料（渐进披露）
    ├── scripts/*.py             # 可选：可复用脚本
    └── assets/*                 # 可选：模板/图片
```

`SKILL.md` frontmatter 契约：

```yaml
---
name: literature-survey            # 必须等于目录名，kebab-case
description: 当需要...时使用；产出...   # 触发条件 + 产出，1-2 句
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [literature, meta]         # 见 §5 tags 列表
---
```

正文必须包含：**何时使用 / 输入输出 / 工作流 / 质量检查清单 / 失败处理 / SciForge 工具对应**，
复杂内容放 `references/` 并在正文引用（渐进披露）。正文超过 5000 字符会被 lint 扣分。

## 3. 使用方式

### 3.1 CLI

```bash
sciforge skills list                       # 列出全部（bundled/installed/external/user）
sciforge skills list --source bundled --tag figure
sciforge skills search "训练 OOM"           # 自然语言检索
sciforge skills show literature-survey     # 查看完整正文
sciforge skills show literature-survey --resources
sciforge skills lint literature-survey     # 0-30 分体检
sciforge skills lint --all
sciforge skills catalog                    # 外部生态目录
```

### 3.2 Agent 工具（对话内按需加载）

| 工具 | 用途 |
|---|---|
| `skill_list` | 列出技能（可按 source/tag 过滤） |
| `skill_search` | 按任务描述检索（如「画模型架构图」） |
| `skill_load` | 加载 SKILL.md 正文 + references 清单 |
| `skill_install` | 安装本地/GitHub/生态目录 skill（需审批） |
| `skill_lint` | 技能质量体检 |

Agent 的推荐行为：任务开始前先 `skill_search`，命中后 `skill_load`，按 skill 工作流执行。

### 3.3 Python API

```python
from sciforge.skills_manager import SkillManager

manager = SkillManager()
manager.list()                        # list[SkillMeta]
manager.get("paper-writing")          # SkillMeta | None
manager.load("paper-writing")         # SKILL.md 全文
manager.search("配图", limit=5)        # 相关 skill
manager.install("./my-skill")         # 装到 ~/.sciforge/skills
manager.install_to("literature-survey", "dsh")   # 装到 ~/.dsh/skills
manager.lint("paper-writing")         # {"ok", "score", "issues", ...}
```

## 4. 安装到 DSH / Claude / Codex / 项目

### DSH（DeepSeek Harness，重点）

```bash
sciforge skills install-dsh literature-survey     # 单个
sciforge skills install-dsh --all                 # 全部 24 个内置 skill
sciforge dsh install                              # 等价入口（若 cli.py 提供）
```

原理：把 `SKILL.md` 连同 `references/`、`scripts/` 复制到 `~/.dsh/skills/<name>/`。
完成后输出提示：**重启 DeepSeek Harness（或新开一个会话）后 DSH 即可发现该 skill**。
DSH 会读取 frontmatter 的 name/description 作为技能触发器，正文按需加载。

### Claude / Codex / 项目目录

```bash
sciforge skills install-dsh literature-survey --target claude   # 需 CLI 支持 --target
```

等价的 Python API：

```python
manager.install_to("literature-survey", "claude")   # ~/.claude/skills
manager.install_to("literature-survey", "codex")    # ~/.codex/skills
manager.install_to("literature-survey", "project")  # <project>/.sciforge/skills
```

目标目录可配置：`~/.sciforge/config.toml`

```toml
[skills]
extra_dirs = ["~/my-skill-collection"]
[skills.targets]
dsh = "~/.dsh/skills"
claude = "~/.claude/skills"
codex = "~/.codex/skills"
```

## 5. 标签（tags）与分类

契约标签（ARCHITECTURE §10）：

`literature` `reading` `ideation` `experiment` `remote` `writing` `figure` `review` `venue` `reproducibility` `meta`

一个 skill 可带多个 tag；lint 对契约外 tag 只给 info 提示、不扣分。

## 6. 安装外部 skill

### 从 GitHub 仓库 / 子目录

```bash
sciforge skills install https://github.com/anthropics/skills --name <skill> --target dsh
sciforge skills install owner/repo --name <skill>              # 简写
sciforge skills install https://github.com/org/repo/tree/main/skills/my-skill
sciforge skills install https://raw.githubusercontent.com/org/repo/main/skills/x/SKILL.md
```

机制：浅克隆（`git clone --depth 1`）到临时目录 → 定位 SKILL.md（子目录 URL 直接定位）→
复制到目标目录。仓库含多个 skill 时需 `--name` 指定；网络失败会给出本地安装建议。

### 从本地目录 / 压缩包

```bash
sciforge skills install ./my-skill/                # 含 SKILL.md 的目录
sciforge skills install ./skills-collection/       # 集合目录（安装全部）
sciforge skills install ./skills.zip
```

### 从外部生态目录

```bash
sciforge skills catalog                             # 浏览
sciforge skills catalog "论文"                      # 过滤
sciforge skills install k-dense-scientific-agent-skills --catalog --name transformers
sciforge skills install ai-research-skills --catalog --name autoresearch
```

`sciforge/catalog/skills.json` 覆盖：

| 生态 | 内容 |
|---|---|
| `k-dense-scientific-agent-skills` | K-Dense AI 177 个科学计算 skill |
| `ai-research-skills` | Orchestra Research 98 个 AI 科研 skill（双循环/写作/分布式） |
| `awesome-scientific-skills` | InternScience 科研 skill 精选 + skill-metric 评分 |
| `anthropics-skills` / `huggingface-skills` | 官方与 HF 生态参考 |
| `daily-arxiv-ai-enhanced` | 每日 arXiv 中文增强摘要数据源 |
| `instsci` / `scansci-pdf` | 论文获取与机构访问（Rimagination） |
| Rimagination/ScanSci 系列 | `scansci-svg` `easyplot` `easymd` `easyslides` `good-question` `good-story` `easymeta` `chuan-check` `mineru-go` `thu-digitizer` |
| `huggingface-ml-intern` | ML 科研智能体参考实现 |

每条目含 `name/url/description/tags/how_to_install/license/verified`；`verified=true` 表示仓库地址已核实，具体子路径以仓库当前结构为准。

## 7. 写一个新 skill

1. `mkdir -p sciforge/skills_lib/my-skill`，目录名 kebab-case（小写字母/数字/连字符）。
2. 写 `SKILL.md`：frontmatter 必填 `name`（=目录名）、`description`（触发条件+产出）、`version`、`license`，建议 `compatibility`、`metadata.author/tags`。
3. 正文 <5000 字符，按「何时使用 / 输入输出 / 工作流 / 质量检查清单 / 失败处理 / SciForge 工具」组织；给具体命令和检查项，**不要写空泛正确的话**。
4. 详细内容放 `references/*.md`、可复用代码放 `scripts/*.py`，正文用相对路径引用它们。
5. `sciforge skills lint my-skill` 检查到 ≥24 分（满分 30），无 critical 问题。
6. `sciforge skills install-dsh my-skill` 装进 DSH 验证；或 `sciforge skills show my-skill` 检查加载。

### lint 评分规则（0-30）

| 维度 | 检查项（分值） |
|---|---|
| 格式 10 | SKILL.md 存在(2)、目录 kebab(1)、无 README.md(1)、frontmatter 可解析(2)、name=目录(2)、description 非空(1)、description ≤1024 且无 XML(1) |
| 完整性 10 | license(1)、compatibility(1)、metadata.author/tags(1)、tags 非空(1)、含示例(2)、含失败处理(2)、含 references/scripts/assets(1)、正文引用扩展资源(1) |
| 写作结构 10 | 正文 <5k 字符(3)、description 含触发条件(2)、含工作流(2)、含 SciForge 工具对应(2)、含质量检查清单(1) |

`ok = score >= 24 且无 critical 问题`（缺 SKILL.md / frontmatter 损坏 / name 不一致为 critical）。

## 8. 故障排查

| 问题 | 处理 |
|---|---|
| `skills list` 看不到新装的技能 | 检查目录是否在 `~/.sciforge/skills/<name>/SKILL.md`；`SCIFORGE_HOME` 是否指向别处；重新运行 list |
| 装进 DSH 后没生效 | 确认 `~/.dsh/skills/<name>/SKILL.md` 存在；**重启 DSH 或新开会话**；检查 frontmatter name 是否与目录一致 |
| GitHub 安装失败 | 网络/代理问题：浏览器下载 zip 后 `sciforge skills install ./x.zip`；或直接下载 SKILL.md 用本地目录 |
| 仓库包含多个 skill 报错 | 按提示加 `--name <skill>`，或使用 `.../tree/main/skills/<skill>` 子目录 URL |
| lint 分数低 | 按 issues 逐项补：license/compatibility/metadata、示例、失败处理、references、触发条件 |
| 外部 skill 名字不规范 | 安装时会尝试规整 kebab-case；仍建议改目录与 frontmatter 后再分发 |

## 9. 已知限制

- GitHub 安装依赖本机 `git` 与网络；不支持私有仓库认证（可用本地目录替代）。
- `install_to` 复制目录，不跟踪上游更新（重新安装即覆盖）。
- lint 是静态规则评分（参考 skill-metric），不代表内容真实性；引用的命令/工具名需自行核对。
- DSH 的 skill 发现机制以宿主实现为准：本仓库保证目录结构与 frontmatter 兼容，若宿主版本变化请以其文档为准。
- 外部生态条目 URL 已核实仓库级地址；单个 skill 的子路径可能随上游重构变化，安装失败时请按仓库当前结构使用子目录 URL。
