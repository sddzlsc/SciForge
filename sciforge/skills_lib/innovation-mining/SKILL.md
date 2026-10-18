---
name: innovation-mining
description: 当已有文献但仍找不到创新点、需要把调研转成可验证的研究假设、或要提升论文贡献度时使用；产出 3-5 个带评分和最小验证方案的候选 idea。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [ideation]
---

# 创新点挖掘（Innovation Mining）

## 何时使用（触发条件）

- 文献调研完成，需要从「别人做了什么」走向「我能做什么」。
- 论文被审稿人质疑贡献不足，需要补充或重述 novelty。
- 课题卡住，需要系统性地扫描空白而非拍脑袋。

## 输入 / 输出

- 输入：`survey.md` / gap 清单 / 近 2 年关键论文 / 计算预算与时间约束。
- 输出：`ideas.md`：3-5 个候选 idea，每个含一句话主张、可证伪假设、最小验证实验、novelty/impact/cost 评分、撞车风险评估。

## 工作流

1. **建 gap 矩阵**：行 = 任务/场景，列 = 效率、泛化、数据效率、可解释性、鲁棒性、评测方法；每格标「已解决 / 部分 / 空白」，空白格就是候选区。
2. **五源找 gap**：论文 Limitation/Future Work、失败案例分析、跨领域方法迁移、新硬件/新数据/新模型、评测盲区（benchmark 饱和、指标可被 hack）。
3. **写可证伪假设**：每个 gap 生成 3 个假设，格式「如果 <机制> 成立，那么在 <设定> 下 <指标> 应提升 ≥x%；若未观察到则假设被否证」。
4. **快速 novelty 反查**：拿假设的关键组合去 `lit_search` 查近 12 个月；撞车则加约束（更小预算、新场景、更强 baseline、理论分析）而不是硬碰。
5. **设计 MVP**：1 张 GPU、≤2 天、单一指标、单一数据集、可对比的 baseline；只验证假设中最不确定的一环。
6. **评分与排序**：`score = novelty(1-5) × impact(1-5) / cost(1-5)`，并单独标注「审稿人会怎么质疑」。
7. **交接**：把胜出的 2 个 idea 交给 `experiment-design` 展开实验矩阵；用 `idea-novelty-check` 做终检。

## 质量检查清单

- [ ] 每个 idea 能用一句话说清「我们证明了 X 在 Y 条件下优于 Z」。
- [ ] 假设可证伪：写明了什么结果会否定它。
- [ ] 有明确 baseline 与数据集，且 baseline 不是稻草人。
- [ ] novelty 已做近 12 个月反查，撞车风险有评级。
- [ ] 有 MVP 实验与失败预案（结果不显著时怎么办）。
- [ ] 贡献不是已有论文的「换数据集/换模型」式换皮。
- [ ] 审稿人一句话质疑可以提前回答。

## 失败处理

| 情况 | 处理 |
|---|---|
| 想到的都被做过 | 沿「约束轴」换维度：预算↓10×、延迟↓、跨模态、跨领域、可解释性、评测协议 |
| 假设无法验证 | 把 claim 收窄成可测量命题；或改为分析型论文（无需训练，理论+实证诊断） |
| 算力/时间不足 | 用 1-2 个中等数据集做 pilot；先证明机制再上大模型 |
| 结果不显著 | 检查指标是否敏感、seeds 是否足够、是否需要看分布而非均值 |
| 多 idea 都一般 | 组合两个弱 gap（如「高效 + 长上下文」）往往产生强故事；或回到调研找冲突结论 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 反查 novelty | `lit_search`、`paper_read`、`paper_compare` |
| 生成/打磨 idea | `sciforge idea --topic "..." --from-library`、`sciforge.ideation` |
| 深挖单篇 gap | `paper-deep-read` skill |
| 终检 | `idea-novelty-check` skill |
| 后续实验 | `experiment-design` skill、`exp_scaffold` |

## 示例

```bash
sciforge idea --topic "长视频扩散模型的时间一致性" --from-library
# 输出后手工补：假设 H1「分层记忆压缩能让 64 帧一致性 FVD 降 15%」→ 交给 experiment-design
sciforge lit search "long video diffusion temporal memory" --since 2025 --source arxiv
```

## 进一步阅读（渐进披露）

novelty 类型学、审稿人视角的贡献分类、常见撞车模式见 `references/novelty-patterns.md`。
