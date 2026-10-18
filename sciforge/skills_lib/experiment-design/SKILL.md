---
name: experiment-design
description: 当需要把研究假设转成可执行实验、设计主表/消融/鲁棒性矩阵、估算 GPU 预算时使用；产出 experiment-plan、运行清单与预期结果表。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [experiment, ideation]
---

# 实验设计（Experiment Design）

## 何时使用（触发条件）

- idea 已定，需要设计能支撑论文 claim 的实验组合。
- 要跑主表/消融/效率对比，但不确定跑哪些、跑几次、怎么算预算。
- 投稿前被质疑实验不充分，需要补实验且时间有限。

## 输入 / 输出

- 输入：2-3 条 claim、候选方法、baseline 列表、可用 GPU 与截止时间。
- 输出：`experiment-plan.md`（claim→实验→指标→判据）、`configs/` 中的 run 配置、GPU-hours 预算表、预期结果表骨架。

## 工作流

1. **claim 驱动**：每条贡献写 1-3 个支撑实验；没有实验支撑的 claim 要么删掉要么降级为 discussion。
2. **固定评测协议**：数据集划分、指标实现（引用官方脚本）、评测脚本版本；所有方法共用同一套，避免「偷偷换评测」。
3. **选 baseline**：至少 2 个，优先官方实现 + 已发表数字；复现不了的 baseline 要注明并给出替代方案。
4. **设计矩阵**：主表（方法 × 数据集 × 3 seeds）、消融（逐模块 on/off + 组合）、效率（参数量/FLOPs/延迟/显存）、鲁棒性（扰动/分布外）、定性样例。
5. **预算估算**：`GPU-hours = 单次时长 × seeds × 变体数`，再乘 1.3 的失败重跑系数；先跑 smoke（小数据/少步数）验证管线。
6. **写成功判据**：例如「主指标提升 ≥2% 且 3 seeds 均值差 > 2×std」；判据在跑之前写死，避免事后挑结果。
7. **执行顺序与 gate**：baseline 复现（gate）→ 单变量 pilot（gate）→ 全量主表 → 消融/效率；每步用 `exp_status/exp_results` 检查后再进入下一步。

## 质量检查清单

- [ ] 每条 claim 都有对应实验与指标，映射关系写进计划。
- [ ] 单变量原则：消融一次只改一个因素。
- [ ] seeds ≥3，报告均值 ± 标准差，不挑最好 seed。
- [ ] 所有方法同数据、同评测脚本、同预算（或明确说明不同预算）。
- [ ] 记录预期结果与失败后的 Plan B，避免跑完才发现无法支撑 claim。
- [ ] GPU 预算含 smoke 与重跑，且留出 30% buffer。
- [ ] 原始日志、config、metrics 有固定保存路径。

## 失败处理

| 情况 | 处理 |
|---|---|
| baseline 复现不了官方数字 | 用官方仓库/issue 排查；仍不行则改用可比 baseline 并如实说明 |
| OOM | 梯度累积、混合精度、梯度检查点、降 batch/序列长度；单卡不够上 `remote-gpu-training` |
| 结果不显著 | 增大 seeds、换更敏感指标、看分布/长尾；必要时下调 claim 强度 |
| 时间不够 | 先跑核心主表 + 关键消融；次要实验放附录或开源后补 |
| 跑一半发现协议错 | 立刻停，记录已消耗预算，修正后重跑并保留失败 run 记录 |
| 多卡结果不可比 | 固定硬件与并行策略，报告中注明 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 生成实验骨架 | `exp_scaffold`、`sciforge exp scaffold --template cv-classification --name my-exp` |
| 启动/查询/停止 | `exp_launch`、`exp_status`、`exp_logs`、`exp_stop` |
| 远程执行 | `remote_run`、`remote_sync`、`gpu_status` |
| 结果汇总制表 | `exp_results`、`figure_table` |
| 参数扫描 | `exp_sweep`（sweep YAML） |

## 示例

```yaml
# experiment-plan 摘要
claims:
  - id: C1
    claim: "分层记忆使 64 帧视频的 FVD 降低 15%"
    experiments: [main_table, ablation_memory]
    metric: fvd
    pass_if: "mean(delta_fvd) <= -15% over 3 seeds"
budget:
  main_table_gpu_hours: 96
  ablation_gpu_hours: 48
  buffer: 0.3
```

## 进一步阅读（渐进披露）

实验计划 YAML 模板、消融矩阵设计与预算估算表见 `references/experiment-spec-template.md`。
