---
name: literature-survey
description: 当需要对一个研究方向做系统性文献调研、摸清 SOTA 与空白、写 related work 时使用；产出可追溯的调研报告、文献库与 gap 清单。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [literature, meta]
---

# 文献调研（Literature Survey）

## 何时使用（触发条件）

- 新选题立项、开题报告、综述写作、related work 章节、投稿前补齐近两年工作。
- 需要判断「这个 idea 是否已有人做过」「SOTA 数字是多少」「该领域主流的 baseline/数据集/指标」。
- 不适用：只需要读某一篇论文 → 用 `paper-deep-read`；只需要看每日新论文 → 用 `daily-arxiv-digest`。

## 输入 / 输出

- 输入：研究主题或问题（越具体越好）、时间窗（默认近 3 年）、venue 范围、必须覆盖的作者/论文、已有文献库。
- 输出：`survey.md`（按技术路线分组的综述）、`gaps.md`（3-5 个可验证的空白）、`references.bib`、PaperLibrary 中已入库论文。

## 工作流

1. **拆检索式**：把主题拆成 3-5 组 `(任务词, 方法词, 数据集/场景, 同义词)`；中英文各准备一版。检索式写进 `survey.md` 附录，保证可追溯。
2. **多源检索**：`lit_search` 同时打 arXiv、OpenAlex、Crossref、HF Papers；每条检索式 `limit 20-30`；最近 6 个月再补一次 `lit_daily`。记录每条查询与命中数。
3. **去重与筛选**：按 arXiv ID / DOI / 归一化标题去重；按「相关度 → 时间 → 引用数」筛出 20-40 篇，`paper_fetch` 拉 PDF 入库。
4. **速读填表**：每篇用 `paper_read` 取 abstract/intro 末段/结论，填五列表：*问题 / 方法一句话 / 数据与设定 / 关键结论数字 / 局限*。
5. **聚类成线**：把文献聚成 4-6 条技术路线，每条写「起点 → 关键改进 → 当前 SOTA → 未解决问题」，而不是按时间流水账。
6. **提炼 gap**：从冲突结论、失效场景、评测盲区、成本瓶颈中各找 1-2 条，每条附 2-3 篇证据论文编号，交给 `innovation-mining`。
7. **产出与归档**：`paper_cite` 生成 BibTeX；`survey.md` 保存到项目 `notes/`，文献入库，gap 清单落到 `.sciforge/runs/`。

## 质量检查清单

- [ ] 每条结论都能点回具体论文（链接可打开），没有凭记忆写的引用。
- [ ] SOTA 数字标注了数据集、指标定义、few-shot / 分辨率等设定，可直接比较。
- [ ] 至少 2 个独立数据源交叉验证同一结论（如 arXiv + S2 元数据）。
- [ ] 近 24 个月的工作覆盖充分；漏检风险在报告里说明。
- [ ] 区分「论文声称（claimed）」与「实测/复现（verified）」。
- [ ] gap 是可证伪的研究问题，不是「效果还能更好」这种空话。

## 失败处理

| 情况 | 处理 |
|---|---|
| 某渠道限流/超时 | 降级到 arXiv + OpenAlex；稍后重试，别阻塞流程 |
| 网络不可用 | 只查本地 PaperLibrary（`library.list/search`），并在报告标注数据截止时间 |
| 检索结果太泛（上千篇） | 加 venue 限制、限定近 2 年、加数据集/方法同义词收窄 |
| 相关文献太少（<8 篇） | 放宽 since；用引文滚雪球（种子论文的 references / citations）；换相邻领域术语 |
| PDF 下载失败 | 只读 abstract；找 arXiv/OpenReview/作者主页 OA 版本，用 `paper-retrieval-oa` |
| 中英文术语不一致 | 建术语对照表，中英文各检一轮 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 多源检索 | `lit_search`、`sciforge lit search "diffusion transformer" --source arxiv --limit 30` |
| 每日/最新补漏 | `lit_daily`、`sciforge lit daily --category cs.CV` |
| 下载入库 | `paper_fetch` |
| 速读 | `paper_read` |
| 引用导出 | `paper_cite`、`sciforge lit cite <arxiv-id>` |
| 一条龙 pipeline | `sciforge pipeline run --recipe survey --topic "..."` |

## 示例

```bash
# 1) 三条检索式并行
sciforge lit search "video diffusion transformer long-context" --source arxiv --since 2023 --limit 30
sciforge lit search "long video generation temporal consistency" --source arxiv --limit 20
sciforge lit search "video diffusion benchmark evaluation" --source openalex --limit 20
# 2) 入库 + 生成 bib
sciforge read 2401.00001 --note
sciforge lit cite 2401.00001 --out references.bib
```

## 进一步阅读（渐进披露）

检索式设计、去重归一化、引文滚雪球和分路线写作模板见 `references/search-strategies.md`。
