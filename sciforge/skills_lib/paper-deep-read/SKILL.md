---
name: paper-deep-read
description: 当需要精读单篇论文、提炼方法细节、准备复现或评审时使用；产出结构化精读笔记、方法伪代码、复现清单与批判要点。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [reading, literature]
---

# 论文精读（Paper Deep Read）

## 何时使用（触发条件）

- 要复现/借用某篇论文的方法，或要判断它能否作为 baseline。
- 审稿、组会汇报、写 related work 时需要准确概括一篇论文。
- 论文很长（>20 页）或公式密集，需要按问题驱动跳读时。

## 输入 / 输出

- 输入：arXiv ID / DOI / PDF / URL；阅读目的（复现、评审、找创新点、写综述）。
- 输出：`notes/<paper-id>.md` 结构化笔记、方法伪代码、复现命令清单、3 条批判点；必要时更新 PaperLibrary。

## 工作流

1. **取全文**：`paper_fetch` 下载 PDF 入库；拿不到 OA 时按 `paper-retrieval-oa` 找机构/预印本版本。
2. **第一遍（5 分钟）**：只读标题、摘要、图表标题、结论。回答：它解决什么问题？核心 claim 是什么？数字是否可信？
3. **第二遍（30 分钟）**：读 Intro 最后两段、Method 全部、Experiments 的设定与主表、Limitation。边读边填模板。
4. **第三遍（按需）**：只对要复现的模块读公式推导、附录超参、代码仓库（记录 commit 与 config 路径）。
5. **结构化笔记**：用 `paper_note` 写入五块——问题 / 洞察 / 方法 / 证据 / 批判；方法部分写 5-10 行伪代码，而不是照抄公式。
6. **追问与对比**：`paper_ask` 问细节（损失函数每一项的作用、ablation 结论、显存/吞吐）；`paper_compare` 与你的 baseline 对齐。
7. **可复现性审计**：列出代码/数据是否公开、缺哪些超参、seed 是否给、硬件要求，标出复现风险等级。

## 质量检查清单

- [ ] 能不看原文用自己的话讲清核心机制（1 分钟版本 + 5 分钟版本）。
- [ ] 知道每个模块被 ablation 掉会发生什么，以及作者是否做了该实验。
- [ ] 记录最关键的超参、数据预处理和评测细节（这些决定能否复现）。
- [ ] 标出作者未验证的 claim / 过拟合 benchmark 的迹象。
- [ ] 笔记里的每个数字都能回到原文的 Table/Figure 编号。
- [ ] 复现清单可直接执行：命令、环境、数据路径、期望指标。

## 失败处理

| 情况 | 处理 |
|---|---|
| 扫描版 PDF 提取不到文本 | 找 arXiv HTML 版 / OpenReview PDF / 出版商 XML；必要时截图存档 |
| 章节切分失败 | 按页读；人工指定 `paper_read --pages 1-8` |
| 公式太多读不动 | 先读伪代码与代码仓库实现，再回看公式对照 |
| 时间有限 | 问题驱动跳读：只读 Method 中与你相关的 1-2 个小节 + 对应实验 |
| 论文与代码不一致 | 以代码为准并记录差异；在笔记里标明「实现偏离论文」的风险 |
| 笔记越写越长 | 正文控制在 1-2 页；推导、原始表格放附录或链接 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 下载全文 | `paper_fetch`、`sciforge read <arxiv-id>` |
| 章节切分与预览 | `paper_read` |
| 结构化笔记 | `paper_note` |
| 论文问答 | `paper_ask` |
| 多篇对比 | `paper_compare` |
| 外部 PDF/机构访问 | `paper-retrieval-oa` skill、`instsci`（如已安装） |

## 示例

```bash
sciforge read 2401.00001 --note            # 下载 + 生成初版结构化笔记
sciforge read 2401.00001 --sections        # 查看切分后的章节预览
sciforge read 2401.00001 --ask "ablation 里去掉位置编码后掉多少点？"
```

## 进一步阅读（渐进披露）

五问速读模板、笔记骨架与批判性检查表见 `references/reading-template.md`。
