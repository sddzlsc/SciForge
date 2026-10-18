# 验收记录（v0.1.0）

> 本文件记录 SciForge 交付时的可复现验证。所有命令在仓库根目录执行。

## 1. 一键验证

```bash
bash scripts/verify.sh
# 输出末尾：全部验证通过 ✓
```

覆盖：模块导入、全量 pytest、离线 demo（含 pipeline/图/论文骨架）、45 个工具、GPU 本地降级、LLM Mock 路由、Web 健康检查。

## 2. 测试

```bash
.venv/bin/python -m pytest -q
# 326 passed, 2 skipped（integration 默认跳过）

SCIFORGE_RUN_INTEGRATION=1 .venv/bin/python -m pytest -m integration -q
# 真实 arXiv / daily-arXiv 网络集成
```

| 测试文件 | 覆盖 |
|---|---|
| test_config / test_registry / test_llm / test_agent | 配置合并、工具契约、DeepSeek/OpenAI 兼容解析、Agent 工具循环 |
| test_pipeline / test_ideation | Recipe 编排、中断续跑、research-state/findings/log、Idea Card |
| test_library / test_literature | 文献库 CRUD、arXiv/daily/S2 解析、BibTeX、PDF 提取（MockTransport） |
| test_remote / test_experiments | SSH 命令构造、nvidia-smi/squeue/sacct 解析、run.json 状态机、结果汇总 |
| test_figures / test_writing | 架构图 SVG 合法性、三线表、LaTeX 工程、BibTeX 去重、check |
| test_skills | Skill 解析/lint/安装/DSH 目标隔离/外部目录 |
| test_web | SSE 对话、pipeline 轮询、目录穿越防护、脱敏、静态页、文件上传/附件注入/GPU 上传 |

GUI 实跑验收（2026-10-18）：`sciforge gui` 启动后 headless Chrome 逐页截图；
`POST /api/chat` 返回 `meta/stage/llm_delta/...` 事件流，`POST /api/pipeline/run` 后台完成并轮询到 `done`。

## 3. 产物

| 产物 | 路径 |
|---|---|
| 架构图（SVG/PNG + spec） | `sciforge/templates/figures/outputs/fig1_architecture.*`、`fig3_transformer_block.*` |
| 实验曲线（PDF/SVG/PNG） | `sciforge/templates/figures/outputs/fig2_curves.*` |
| 三线表（LaTeX/Markdown） | `sciforge/templates/figures/outputs/table_main_results.*` |
| GUI 实跑截图 | `examples/demo/screenshots/live-console-*.png` |
| Web 截图（早期） | `examples/demo/screenshots/console-*.jpg` |
| 离线演示 | `sciforge demo` → `sciforge-demo/DEMO.md` |
| DSH 技能 | `~/.dsh/skills/*/SKILL.md`（24 个） |

## 4. 真实数据验证（2026-10-18）

- 实时 arXiv 检索：`sciforge lit search "diffusion transformer video generation" --source arxiv --limit 3` → 3 篇 + 写入文献库
- 文献渠道实测（2026-10-18）：arXiv ✅ / daily-arXiv ✅ / OpenAlex ✅ / Crossref ✅ / HF Papers ✅（Semantic Scholar 已按需求从界面移除）
- 全文链路：arXiv PDF 下载成功；Unpaywall 需邮箱；instsci 为可选机构访问；不集成 Sci-Hub（版权风险）
- GUI 配置实测：模型 Provider 新增/测试（Web 端验证脱敏与持久化）、GPU 服务器保存/SSH 失败反馈、文献设置（OA 邮箱/每日语言）保存；`config.toml` 0600
- 文件上传实测：`POST /api/upload` 保存到项目 `uploads/` 并回传文本预览；带附件 mock 对话的任务上下文中出现附件内容；GPU 上传端点以注入式 SSH 执行器验证
- GPU 指标实测：`remote_metrics` 自动发现 + 解析 JSON/JSONL/历史序列；`GET /api/servers/{name}/metrics` 注入式验证；`/api/download` 下载与目录穿越防护验证
- 新增免费源实测：PubMed → 题录+摘要 ✅；Europe PMC ✅；OpenReview（ICLR/NeurIPS）✅；Crossref 出版社过滤（IEEE/ACM/…）✅
- 历史会话：`/api/sessions` 列表 + 详情 + 从 transcript 恢复上下文继续对话，均有测试覆盖
- daily-arXiv-ai-enhanced：`sciforge lit daily --category cs.CV --limit 3` → 中文增强摘要 + 文献库
- 示例论文精读：`sciforge read examples/demo/fixtures/paper.pdf` → pymupdf 提取 4 页、章节结构完整
- 实验骨架：`sciforge exp scaffold --template cv-classification` → `bash scripts/smoke.sh` → `SMOKE OK`
- 论文工程：`sciforge write draft --venue cvpr` → 5 章节 + refs.bib；`sciforge write check` 输出逐行问题

## 5. 已知限制

- 真实 SSH/Slurm 未在真机验证（无服务器环境）；命令构造与解析由 116 个单测覆盖
- 本机无 LaTeX：论文编译走优雅降级并给出安装提示；架构图 PDF 已通过 macOS `sips` 兜底导出（Linux 建议装 librsvg/cairosvg）
- LLM 质量决定产出质量；`--mock` 仅用于流程演示
- 会议模板（CVPR/NeurIPS/ICLR/ACL）需自备官方 `.sty`；generic 模板零依赖可直接编译
