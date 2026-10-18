# SciForge 离线 Demo Fixtures

本目录是 **完全离线**、可复现的演示数据，供 `sciforge` 的文献、精读、实验、配图、写作与
Web 控制台在没有网络 / 没有 API key 时也能完整跑通「文献 → 精读 → 创新点 → 实验 → 图 → 论文」。

> 所有论文均为真实公开论文（arXiv 元数据）或 demo 虚构论文；数值仅用于演示流程，
> 不是任何真实论文的复现结果。fixtures 不联网、不含密钥。

## 文件清单

| 文件 | 说明 | 消费方 |
|---|---|---|
| `arxiv_search.json` | 模拟 arXiv 检索结果：9 篇真实论文（ViT / CLIP / DDPM / LDM / DiT / Video Diffusion / Sora 综述 / LoRA / Transformer）。字段与 arXiv Atom API 对齐（`id`、`title`、`authors[{name}]`、`summary`、`published`、`updated`、`primary_category`、`categories`、`abs_url`、`pdf_url`、`comment`、`venue`），另加 `summary_zh` / `tags` 两个 demo 扩展字段。 | `lit_search` 离线回退、Web 文献页 |
| `daily_arxiv.jsonl` | 3 行 daily-arXiv-ai-enhanced 格式数据：`id` / `title` / `authors` / `summary` / `comment` / `primary_category` / `categories` / `published` / `arxiv_url` / `pdf_url` + `AI_summary` / `AI_keywords` / `AI_tldr` 中文增强字段。 | `lit_daily` 离线回退、Web「每日 arXiv」 |
| `paper.pdf` | **4 页**示例论文《Adaptive Token Routing for Efficient Video Diffusion Transformers》，含 Abstract / 1 Introduction / 2 Related Work / 3 Method / 4 Experiments（两张三线表）/ 5 Conclusion / References。由 `pymupdf` 生成。 | `paper_read` / `paper_ask` / `paper_note` |
| `paper.txt` | 同一篇论文的纯文本版本（便于不依赖 PDF 解析的测试与 RAG 演示）。 | 同上 |
| `paper_preview.png` | `paper.pdf` 第 1 页的 PNG 缩略图（由脚本一并生成），供 Web 图表页离线预览。 | Web 图表页 |
| `arch_spec.yaml` | Figure 1 模型架构图 DSL（`nodes` / `edges` / `groups`，符合 `docs/ARCHITECTURE.md` §11）。 | `sciforge fig arch --spec ...` |
| `arch_preview.svg` | 由 `sciforge.figures.arch` 从 `arch_spec.yaml` 渲染出的预览 SVG，供离线 Web 图表页展示（可随时重新生成）。 | Web 图表页 |
| `plot_spec.yaml` | 实验结果曲线 spec（line、3 条曲线、3 seeds 均值±方差、NeurIPS 风格）。 | `sciforge fig plot --spec ...` |
| `results/metrics.json` | 主结果表（5 行方法对比）、消融表、效率指标（FVD / IS / VBench / 吞吐 / GPU-hours）、router 统计、训练曲线数据。 | 写作 / 三线表 / 结果分析 |
| `README.md` | 本文件。 | — |

## 生成 / 校验

`paper.pdf` 与 `paper.txt` 由脚本生成（其余文件随仓库提交）：

```bash
.venv/bin/python examples/demo/make_fixtures.py          # 重新生成 PDF + TXT 并校验全部 fixtures
.venv/bin/python examples/demo/make_fixtures.py --check  # 只校验，不重新生成
```

依赖 `pymupdf`（`uv pip install -e ".[dev]"` 已包含）。生成结果确定性可复现（无随机性）。

## 用法示例

```bash
# 1. 文献检索（离线时由 fixtures 兜底；也可直接看 JSON）
sciforge lit search "diffusion transformer" --source arxiv --json

# 2. 精读示例论文
sciforge read examples/demo/fixtures/paper.pdf --note

# 3. 生成 Figure 1 架构图 / Figure 2 曲线
sciforge fig arch  --spec examples/demo/fixtures/arch_spec.yaml  -o figures/fig1_arch.svg
sciforge fig plot  --spec examples/demo/fixtures/plot_spec.yaml  -o figures/fig2_curves.pdf

# 4. 用实验数据生成论文三线表
sciforge fig table --spec examples/demo/fixtures/results/metrics.json -o tables/table1.tex

# 5. 全流程（离线 Mock，无需 API key）
sciforge pipeline run --recipe full --topic "自适应 token 路由的视频扩散模型" --mock

# 6. Web 控制台（只监听 127.0.0.1:8765）
sciforge serve
```

## Web 控制台中的离线演示

启动 `sciforge serve` 后打开 `http://127.0.0.1:8765`：

1. **文献页**：真实 `lit_search` 不可用时自动展示本目录的 `arxiv_search.json` + `daily_arxiv.jsonl`，
   卡片带「精读 / 加入库」按钮；
2. **对话页**：默认离线 Mock，点击快捷任务 chips（文献调研 / 每日 arXiv / 读论文 / 生成模型图 / 工具演示 / 周报）；
   其中「工具演示」会让 Agent **真实执行**一次 `read_file`（读取本目录 `paper.txt`），用于展示工具调用折叠卡片；
3. **图表页**：`arch_preview.svg`、`paper_preview.png`、`paper.pdf`、`arch_spec.yaml`、`plot_spec.yaml`、`results/metrics.json`
   会以预览卡片 / 文件列表出现（通过安全的 `/figures`、`/files` 端点）；
4. **实验页**：选择 recipe + 主题 → mock 运行 pipeline，轮询阶段进度，查看 `REPORT.md` 等产物；
5. **技能页**：列出 `sciforge/skills_lib` 技能，可一键「安装到 DSH」。

## 字段约定备注

- `arxiv_search.json` 顶层：`{source, query, generated_at, note, total, results[]}`；
- `daily_arxiv.jsonl` 每行一个 JSON 对象（不是数组），与 daily-arXiv-ai-enhanced 的
  `<date>_AI_enhanced_Chinese.jsonl` 对齐；
- `plot_spec.yaml` 的 `series` 同时给出 `y`/`yerr`（matplotlib 常用）与 `mean`/`std`
  （部分实现使用）两种命名，绘制时选用其一即可；
- `metrics.json` 的 `main_results` / `ablations` 可直接用于生成 booktabs 三线表。
