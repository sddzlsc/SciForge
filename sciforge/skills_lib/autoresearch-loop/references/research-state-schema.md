# 研究状态文件与双循环协议

## 1. research-state.yaml

```yaml
project: <slug>
topic: <一句话研究问题>
stage: bootstrap          # bootstrap | inner | outer | finalize
created_at: <ISO8601>
updated_at: <ISO8601>
active_hypotheses:
  - id: H1
    claim: <可证伪的一句话主张>
    status: proposed | testing | supported | rejected | parked
    experiments: [exp001, exp004]
    next_action: <下一步具体动作>
    prediction: <如果假设成立应观察到什么>
findings:
  supported: []
  rejected: []
  open_questions: []
budget:
  gpu_hours_used: 0
  gpu_hours_limit: 400
  deadline: <date>
artifacts:
  literature: notes/
  experiments: experiments/
  results: results/
  figures: figures/
  paper: paper/
last_outer_loop: null
next_action: <会话恢复后的第一条命令/动作>
```

## 2. research-log.md

按时间追加，只记**决策**而非流水：

```markdown
## 2025-06-01 14:20 — 外循环 #2
- 观察: H1 在 3 seeds 上平均降 11%，未达 15% 阈值；长序列方差大。
- 决策: 保留 H1 但下调 claim 为 10-12%；新增 H3（memory 压缩率敏感性）。
- 放弃: H2（跨数据集迁移），因为 dev 上无收益且预算有限。
- 下一步: 跑 H3 的 3 档压缩率；更新 findings。
```

## 3. findings.md

```markdown
# Findings（截至 <date>）

## 已支持
- 分层记忆在 32-64 帧上稳定降低 FVD（exp001/004/007，3 seeds）。
## 被否证
- 「压缩率越高越好」不成立：>8× 压缩后细节指标下降（exp009）。
## 待验证
- 128 帧是否仍有效？（H4）
## 叙事草案
<一段 200 字的论文故事：问题→洞察→证据→意义，随实验更新>
```

## 4. 双循环节奏

| 循环 | 频率 | 输入 | 输出 | 退出条件 |
|---|---|---|---|---|
| 内循环 | 1-3 天/实验 | 当前假设 + protocol | run 结果 + analysis | 5-10 个实验或模式出现 |
| 外循环 | 每 5-10 实验 / 停滞时 | 全部结果 + findings | 新假设/方向调整/叙事更新 | 新假设可验证 或 明确停止 |
| 收口 | claim 稳定后 | 主结果 + 消融 | 论文草稿 + 自检 | 投稿材料齐备 |

## 5. 续跑协议

1. 读 `research-state.yaml` 的 `stage`、`active_hypotheses`、`next_action`。
2. 读 `research-log.md` 最近 20 条决策。
3. 用文件系统核对 completed_runs 是否真的存在对应 `experiments/<h>/results/`。
4. 若状态与文件不一致，以文件为准回填状态并记录修正。
5. 从 `next_action` 直接继续，不重新调研已完成部分。
6. 更新状态后再结束本轮，确保下轮可续。

## 6. 反模式

- 只写日志不写分析 → 无法形成 findings。
- 同时开 5+ 假设 → 哪条都推不动；限制 ≤3。
- 外循环跳过 → 变成无脑跑实验，没有故事。
- 状态文件手工漂移 → 每次写「下一步」并核对文件系统。
