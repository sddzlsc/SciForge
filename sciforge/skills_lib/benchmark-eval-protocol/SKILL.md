---
name: benchmark-eval-protocol
description: 当需要设计或审查评测协议、防止数据泄漏与指标作弊、保证与 SOTA 可比时使用；产出可复用的评测协议与泄漏检查清单。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [experiment]
---

# 评测协议（Benchmark & Eval Protocol）

## 何时使用（触发条件）

- 新方法要报告指标，需要确定用哪个 benchmark、哪个 split、哪个评测脚本。
- 结果异常好，怀疑数据泄漏、测试集调参或评测实现不一致。
- 需要复现他人 SOTA 数字，或设计新 benchmark/新指标。

## 输入 / 输出

- 输入：任务类型、目标 benchmark、baseline 论文与其报告设置、可用数据与算力。
- 输出：`eval-protocol.md`（数据划分/指标定义/评测命令/提交格式）、泄漏检查记录、可比的 SOTA 对照表。

## 工作流

1. **对齐官方协议**：从 dataset 官网/论文附录拿 split、指标实现（官方 eval 脚本版本）、prompt/后处理规则；不要自己「改良」评测。
2. **固定评测面**：test split 只用一次；开发用 dev；超参选择只能看 dev；最终报告多 seed。
3. **指标实现校验**：用官方脚本复算 baseline 的已发表数字；若对不上，先查版本/预处理/后处理，再报数。
4. **泄漏检查**：训练集与测试集去重（精确 + 近重复 embedding）；检查测试集是否出现在预训练语料；检查 prompt 模板泄露答案；检查数据划分随机性。
5. **公平比较**：同参数量/同训练数据/同评测预算分档报告；不同档位明确标注（如 7B vs 70B）。
6. **报告规范**：均值 ± std、seeds 数、硬件、评测代码 commit；显著性检验（配对 bootstrap/t-test）。
7. **可复现**：评测命令脚本化，结果写 `results/<benchmark>/<model>.json`，与表格数字一一对应。

## 质量检查清单

- [ ] split 与官方一致，test 没有被用于调参。
- [ ] 指标实现来自官方脚本，版本记录在案。
- [ ] baseline 数字来自复现或同协议引用，不是不同设置拼凑。
- [ ] 训练/测试去重与污染检查已完成。
- [ ] 多 seed 报告均值±std；显著性检验合理。
- [ ] 参数量/数据量/推理预算公平或明确分档。
- [ ] 评测命令可一键重跑，结果 JSON 与论文表格一致。
- [ ] 失败案例/边界情况有记录。

## 失败处理

| 情况 | 处理 |
|---|---|
| 复现数字低于论文 | 查预处理/prompt/后处理/版本；联系作者；用官方 issue 检索 |
| 结果好得不真实 | 立即查泄漏：重复样本、test 参与训练、prompt 泄漏、指标实现 bug |
| 指标不可比 | 统一评测脚本重跑所有方法；不同设置分表报告 |
| 测试集太小/方差大 | 多 seed + bootstrap CI；报告置信区间而非单点 |
| benchmark 饱和 | 补更难的子集/新指标；说明饱和对结论的影响 |
| 评测成本高 | 先小样本校准，再全量；缓存模型输出避免重复推理 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 跑评测 | `remote_run`、`exp_launch`、`exp_results` |
| 结果汇总 | `exp_results`、`figure_table`、`figure_plot` |
| 去重/污染检查 | 本地脚本 + `dataset-audit` skill |
| 论文引用 | `paper_cite`、`lit_search`（确认 baseline 设置） |

## 示例

```bash
# 同一评测脚本重跑 baseline 与 ours，输出可 diff 的 json
sciforge exp launch --server gpu1 --script eval.py --name eval-baseline
sciforge exp launch --server gpu1 --script eval.py --name eval-ours
sciforge exp results eval-baseline eval-ours --out results/eval/
```

## 进一步阅读（渐进披露）

数据泄漏排查清单、显著性检验与公平比较细则见 `references/leakage-checklist.md`。
