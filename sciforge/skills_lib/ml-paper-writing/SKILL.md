---
name: ml-paper-writing
description: 当需要按 NeurIPS/ICML/ICLR/CVPR/ACL 顶会标准写作、核对引用真实性、组织段落论证时使用；产出可投稿的论文结构与经核验的参考文献。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [writing, venue]
---

# 顶会论文写作方法论（ML Paper Writing）

## 何时使用（触发条件）

- 要投 NeurIPS/ICML/ICLR/CVPR/ACL/AAAI/COLM 等顶会，需要按审稿人期待组织论文。
- 已有结果，需要把「做了什么」写成「为什么重要、为什么可信」。
- 需要核验引用、避免引用幻觉，或重构段落逻辑。

## 输入 / 输出

- 输入：研究 repo/实验记录、claim 列表、目标 venue 与页数、已有草稿（可选）。
- 输出：逐节草稿、段落级论证结构、经 `paper_cite` 核验的 BibTeX、投稿前 checklist 结果。

## 工作流

1. **确定 one-sentence contribution**：一句话说清「我们证明了什么」；下面所有内容服务它。
2. **倒金字塔写 Intro**：领域重要 → 现有方法在什么设定下失败（引用）→ 我们的洞察 → 方法一句话 → 贡献列表 → 结果预告。
3. **Method 按「问题-设计-为什么有效」写**：每个模块先给动机，再给公式/结构，最后给直觉或理论解释；Figure 1 先讲全貌。
4. **Experiments 按 claim 组织**：每条 claim 一小节——设置、结果表/图、解读（不是复述数字）；消融回答「每个设计是否必要」。
5. **Related Work 分组对比**：3-5 条路线，每段结尾一句「In contrast, ...」；不贬低他人。
6. **引用必须程序化核验**：`paper_cite` / `lit_search` 取元数据生成 BibTeX；不确定就标 `[CITATION NEEDED]`；绝不凭记忆写引用。
7. **段落级打磨**：每段第一句是论点（topic sentence）；一段只讲一件事；删掉 "very/novel/significantly" 等空词；数字具体化。
8. **投稿前自检**：`paper_review` 输出 Major/Minor；对照 `latex-venue-submission` 的 checklist 与页数限制。

## 质量检查清单

- [ ] Abstract/Intro 贡献/Conclusion 三处 claim 一致且被实验支撑。
- [ ] 每条引用都来自检索/元数据接口，无幻觉；年份/venue 正确。
- [ ] Related Work 每组都有与本文的差异句。
- [ ] 每节开头有 roadmap，段落有 topic sentence，章节间过渡自然。
- [ ] 主表/图与正文数字一致；先引用后出现。
- [ ] 有 limitation/失败分析，主动回应审稿人预期质疑。
- [ ] 写作符合 venue 风格（页数/模板/参考文献格式）。
- [ ] 让同方向同学读 Intro 后能复述贡献。

## 失败处理

| 情况 | 处理 |
|---|---|
| 引用不确定 | 检索核验；找不到就标 `[CITATION NEEDED]`，绝不编造 |
| 说服力不足 | 回到 claim→实验映射，补关键消融/对比实验 |
| 故事线散 | 用 one-sentence contribution 重排章节；删掉不服务主线的内容 |
| 超页数 | 细节进附录、压缩 Related Work、合并图 |
| 审稿人视角弱 | 自问「最可能被攻击的点」，提前写进 limitation 或补实验 |
| 英文表达差 | 每段 topic sentence + 主动语态 + 具体数字；避免长从句堆叠 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 工程与编译 | `paper_init`、`paper_compile`、`sciforge write` |
| 章节生成 | `paper_section` |
| 引用核验 | `paper_cite`、`lit_search`、`paper_fetch` |
| 自检 | `paper_review`、`sciforge write review` |
| 图表 | `figure_architecture`、`figure_plot`、`figure_table` |

## 示例

```bash
sciforge write draft --venue iclr --title "Memory-Efficient Long Video Diffusion"
sciforge write cite 2401.00001 2402.00002 --paper paper/
sciforge write review --paper paper/ --venue iclr
```

## 进一步阅读（渐进披露）

段落级论证模板、引用核验流程与顶会写作反模式见 `references/paragraph-blueprints.md`。
