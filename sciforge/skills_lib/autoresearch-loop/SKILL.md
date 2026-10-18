---
name: autoresearch-loop
description: 当需要自主推进一个完整科研项目、用双循环（实验迭代 + 反思综合）管理多假设研究、或需要可续跑的 research-state 时使用；产出持续更新的状态文件、findings 与阶段报告。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [meta, ideation, experiment]
---

# 自主科研双循环（Autoresearch Loop）

## 何时使用（触发条件）

- 需要让 agent 连续多轮推进一个研究项目（从调研到实验到写论文），而不是一问一答。
- 有多个假设要并行/串行验证，需要记录状态、避免重复劳动、跨会话续跑。
- 想用「内循环跑实验 + 外循环找故事」的节奏管理长周期研究。

## 输入 / 输出

- 输入：研究主题或问题、计算预算、已有文献/代码/结果、续跑时的状态目录。
- 输出：`research-state.yaml`（当前阶段/假设/进度）、`research-log.md`（决策时间线）、`findings.md`（不断演化的结论叙事）、阶段报告（实验表/图/论文草稿）。

## 工作流

1. **初始化工作区**：`notes/`（文献与笔记）、`experiments/<hypothesis>/`（protocol/code/results/analysis）、`results/`（原始指标/CSV）、`figures/`、`paper/`、状态文件三件套（见 references）。
2. **Bootstrap（一次）**：界定问题 → `literature-survey` 调研 → 形成 2-4 个初始假设 → 写 `research-state.yaml`（stage=bootstrap→inner）。
3. **内循环（快，反复）**：选一个假设 → 写 protocol（预测结果）→ `exp_scaffold`/`exp_launch` 跑实验 → `exp_results` 收指标 → 记录到 results/ 与 analysis.md → 更新状态。
4. **外循环（周期，反思）**：每 5-10 个实验或遇到停滞时，回看所有结果找模式 → 更新 `findings.md`（结论/反例/新问题）→ 生成新假设或调整方向 → 记录到 research-log。
5. **回到文献**：结果与预期不符时，`lit_search`/`paper_read` 补上下文；把发现写进 literature/ 与 findings.md。
6. **收口**：claim 稳定后转 `experiment-design` 补齐主表/消融 → `paper-writing` 写稿 → `paper_review` 自检 → 归档状态。
7. **续跑**：新会话先读 `research-state.yaml` + 最近 20 条 research-log，从 next_action 继续，不重复已完成的实验。

## 质量检查清单

- [ ] `research-state.yaml` 记录了阶段、假设、已完成实验、下一步（可被新会话直接续跑）。
- [ ] 每个实验有 protocol（预写预测）与 analysis（结果解释），不是只留日志。
- [ ] research-log 记录关键决策与理由（为什么换方向、为什么砍假设）。
- [ ] findings.md 是演化的叙事，不是实验流水账。
- [ ] 每轮外循环至少产出一个可验证的新假设或明确的停止理由。
- [ ] 失败实验也有记录（避免重复踩坑）。
- [ ] 预算消耗（GPU-hours/时间）在状态文件里可见。
- [ ] 随时能回答：当前最强 claim 是什么、证据是哪几个实验。

## 失败处理

| 情况 | 处理 |
|---|---|
| 实验一直失败 | 外循环介入：分层排查（数据/实现/假设）；考虑换假设而非硬调 |
| 陷入局部最优 | 强制回到文献 + `research-ideation` 换视角；扩大/收窄问题 |
| 状态文件与真实进度不一致 | 以文件系统（experiments/results）为准，回填状态并记录修正 |
| 会话中断/上下文丢失 | 依赖三件套恢复；每次写状态用「下一步动作」字段 |
| 假设太多管不过来 | 限制同时活跃 ≤3 个；冻结低优先级假设并标注原因 |
| 反复重复同一实验 | 状态文件记录 run_id + config hash；启动前查重 |
| 预算将耗尽 | 切换到收口模式：只跑支撑核心 claim 的实验，其余写 limitation |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 文献 | `lit_search`、`paper_read`、`literature-survey` skill |
| 假设生成 | `innovation-mining`、`research-ideation` skill、`sciforge idea` |
| 实验 | `exp_scaffold`、`exp_launch`、`exp_status`、`exp_results`、`remote_run` |
| 流水线 | `sciforge pipeline run --recipe full --topic "..."`（阶段化推进） |
| 写作收口 | `paper-writing` skill、`paper_section`、`paper_review` |
| 状态文件 | `research-state.yaml` / `research-log.md` / `findings.md`（见 references） |

## 示例

```yaml
# research-state.yaml（节选）
project: long-video-memory
stage: inner            # bootstrap | inner | outer | finalize
active_hypotheses:
  - id: H1
    claim: "分层记忆把 64 帧 FVD 降 15%"
    status: testing
    next_action: "run exp_mem_v2 on 3 seeds"
completed_runs: [exp001, exp002, exp003]
budget: {gpu_hours_used: 42, gpu_hours_limit: 400}
last_outer_loop: 2025-06-01
```

## 进一步阅读（渐进披露）

三件套状态文件模板、双循环节奏与续跑协议见 `references/research-state-schema.md`。
