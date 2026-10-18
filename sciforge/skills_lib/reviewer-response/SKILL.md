---
name: reviewer-response
description: 当收到审稿意见需要写 rebuttal 或修改稿回复时使用；产出逐条回应、修改证据清单与礼貌有据的回复信。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [review, writing]
---

# 审稿回复（Reviewer Response / Rebuttal）

## 何时使用（触发条件）

- 收到会议 rebuttal 窗口、期刊 major/minor revision 意见，需要逐条回复。
- 需要判断哪些意见必须补实验、哪些可以解释、哪些应礼貌反驳。
- 需要把修改内容映射到具体章节/图表/实验编号。

## 输入 / 输出

- 输入：审稿意见原文（编号）、论文当前版本、实验与图表清单、回复字数/页数限制。
- 输出：`response.md`（逐条编号回复）、修改清单（章节/图/表/实验 → 改动）、补实验计划与优先级。

## 工作流

1. **分类拆解**：把每条意见拆成「事实问题 / 方法质疑 / 实验缺失 / 写作问题 / 误解」；标注必须补实验（E）、需解释（C）、仅文字修改（T）。
2. **先修致命项**：审稿人共同提出的问题（≥2 人）优先；方法/实验类优先于写作类。
3. **逐条回复结构**：引用意见要点（缩写）→ 感谢 → 回应（做了什么/为什么）→ 证据（章节/图表/实验编号/数字）→ 修改位置。
4. **补实验**：按 `experiment-design` 快速设计最小补实验；时间不够就在回复中说明并给出已有证据。
5. **礼貌反驳**：承认对方视角，用证据与引用说明；避免「审稿人错了」式措辞。
6. **一致性检查**：回复中承诺的修改必须真的出现在稿子里；所有数字与新版论文一致。
7. **控制篇幅**：每个问题先给结论句（加粗），细节放后面；按严重性排序。

## 质量检查清单

- [ ] 每条意见都有编号与对应回复，无遗漏。
- [ ] 回复中的「我们已修改」都能在论文中定位（Section/Figure/Table 编号）。
- [ ] 承诺的补实验有结果或明确说明限制。
- [ ] 异议回复基于证据与引用，不情绪化。
- [ ] 数字、术语、缩写与修改稿一致。
- [ ] 遵守回复字数/页数限制；图表可复现。
- [ ] 修改稿的贡献表述与 rebuttal 一致，未偷换 claim。

## 失败处理

| 情况 | 处理 |
|---|---|
| 时间不够补实验 | 优先补最关键的一组；用现有分析/小规模实验回应，并在 limitation 中如实说明 |
| 审稿人误解方法 | 先承认表述不清，再在回复与修改稿中补一句精确定义 + 图示 |
| 两位审稿人意见冲突 | 分别回应并解释取舍依据；修改稿中兼容两者（如加附录讨论） |
| 被质疑 novelty | 引用 concurrent work 对比表，明确差异；必要时调整 claim 范围 |
| 被要求引用某文 | 核实相关性后引用；不相关则礼貌说明并给出替代 |
| 回复超字数 | 先压缩写作类回复；实验类保留证据句 + 附录链接 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 解析审稿意见 | `paper-review` 文本整理；`paper_read` 查相关文献 |
| 补实验 | `experiment-design` skill、`exp_scaffold`、`exp_launch` |
| 修改论文 | `paper_section`、`paper_compile` |
| 自检 | `paper_review`、`sciforge write review` |
| 引用核验 | `paper_cite`、`lit_search` |

## 示例

```text
R1-W2: "The ablation lacks a comparison with the memory-free variant at equal FLOPs."
回应: 感谢指出。我们补充了等 FLOPs 设置下的对照（Table 4 第 3 行）：
去除 memory 模块后 FVD 从 41.2 升至 48.7（+18.2%），而 FLOPs 相同。
修改位置: Sec. 4.3, Table 4, 回复 P2。
```

## 进一步阅读（渐进披露）

回复信模板、分类话术与补实验优先级表见 `references/response-template.md`。
