---
name: paper-writing
description: 当需要从实验结果与调研材料写出论文草稿、逐节打磨或做投稿前自检时使用；产出结构化 LaTeX 草稿、图表引用与审稿式自检报告。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [writing]
---

# 论文写作（Paper Writing）

## 何时使用（触发条件）

- 实验与图表已就绪，需要从零起草论文（Intro/Method/Experiments/Related Work）。
- 已有草稿但结构松散、claim 与实验不对齐、或需要压缩/扩写章节。
- 投稿前需要检查引用完整性、图表编号、占位符与行文一致性。

## 输入 / 输出

- 输入：`results/`（metrics/表格）、`figures/`、`survey.md`、`ideas.md`、venue 与页数限制。
- 输出：`paper/` LaTeX 工程（main.tex + sections/*.tex + refs.bib）、图表引用、自检报告 `review.md`。

## 工作流

1. **先写骨架**：定 claim（1-3 条）、贡献列表、Section 标题与每节一句话主题；列出需要的图表与实验编号。
2. **先写 Method 与 Experiments**：这两节决定论文可信度；Method 用「问题→设计→为什么有效」组织，Experiments 按 claim 顺序展开。
3. **再写 Intro**：倒金字塔——领域重要性 → 现有方法局限（引用）→ 本文洞察 → 贡献列表 → 结果预告。最后一段贡献与 abstract 精确对应。
4. **Related Work 最后写**：按技术路线分组，每段「他们做了什么 → 与我们的区别」，避免逐篇流水账。
5. **用工具生成**：`paper_section` 生成/改写单节；`paper_cite` 走检索核验后生成 BibTeX；`figure_*` 产出图表后引用编号。
6. **压缩与打磨**：先砍修饰词，再合并重复句，最后调图/表位置；每段首句必须是该段论点（topic sentence）。
7. **自检**：`paper_review` 输出 Major/Minor 清单；`paper_compile` 编译并修 undefined reference / 占位符。

## 质量检查清单

- [ ] Abstract、Intro 贡献列表、Conclusion 三处 claim 完全一致且可被实验支撑。
- [ ] 每条 claim 都能指向具体表格/图/实验编号。
- [ ] 引用全部来自论文库或检索结果，无凭记忆编造；未核实处标 `[CITATION NEEDED]`。
- [ ] Related Work 每组都有「与本文差异」句，不贬低他人工作。
- [ ] 图表在正文中先引用后出现；图注自洽；数字与 `metrics.json` 一致。
- [ ] 术语、符号、缩写全文一致（首次出现给定义）。
- [ ] 页数/字体/参考文献格式符合 venue 模板；无 TODO、占位符、编译警告。
- [ ] 至少一位「模拟审稿人」读摘要+Intro 后能说出贡献。

## 失败处理

| 情况 | 处理 |
|---|---|
| 不知道从哪写起 | 先写 Method 的问题-设计-效果三段；再写 Experiments 的「claim→证据」表 |
| 贡献说不清 | 回到 `innovation-mining` 的一句话主张模板重写 |
| 引用找不到/不确定 | 用 `paper_cite` 检索核验；找不到就标 `[CITATION NEEDED]`，绝不编造 |
| 超页数 | 先移推导/细节到附录，再压缩 Related Work 与重复表述；图表改双栏 |
| 编译失败 | `paper_compile` 查看首个错误；常见是未定义引用/缺包/中文编码（用 xelatex + ctex） |
| 被说写作差 | 每段 topic sentence + 主动语态 + 数字具体化；删掉 "very/novel/significantly" 等空词 |
| 审稿意见难回应 | 转 `reviewer-response` skill，按意见编号逐条回复 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 初始化 LaTeX 工程 | `paper_init`、`sciforge write draft --venue cvpr`、`sciforge write init` |
| 生成/改写章节 | `paper_section` |
| 引用与 BibTeX | `paper_cite`、`lit search` + `paper_fetch` |
| 编译 | `paper_compile`、`sciforge write compile` |
| 自检 | `paper_review`、`sciforge write review` |
| 图表 | `figure_architecture` / `figure_plot` / `figure_table` |

## 示例

```bash
sciforge write draft --venue neurips --title "Long-Context Video Diffusion"
sciforge write section method --paper paper/ --notes notes/method.md
sciforge write cite 2401.00001 --paper paper/
sciforge write compile --paper paper/
sciforge write review --paper paper/ --results results/
```

## 进一步阅读（渐进披露）

各 venue 的页数/模板/评审偏好与段落级写作模板见 `references/venue-checklist.md`，更细的顶会写法见 `ml-paper-writing` skill。
