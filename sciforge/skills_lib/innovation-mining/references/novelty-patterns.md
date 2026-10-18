# 实验计划模板

## 1. experiment-plan.yaml

```yaml
topic: <一句话主题>
claims:
  - id: C1
    statement: <可证伪主张>
    metric: <主指标>
    pass_if: <例如 "3 seeds 均值提升 >= 2% 且 diff > 2*std">
    experiments: [exp_main, exp_ablation_a]
baselines:
  - name: <官方实现> repo: <url> url: <论文> reproduced: false
datasets:
  - name: <数据集> split: <train/val/test> eval_script: <路径/版本>
matrix:
  main_table: {methods: [...], datasets: [...], seeds: [0,1,2]}
  ablation: {factors: [module_a, module_b], mode: leave-one-out}
  efficiency: {metrics: [params, flops, latency_ms, peak_mem_gb], batch: 1}
budget:
  smoke_gpu_hours: 2
  main_gpu_hours: 96
  buffer: 0.3
artifacts:
  logs: runs/<run_id>/logs
  metrics: runs/<run_id>/metrics.json
  checkpoints: /data/checkpoints/<run_id>
```

## 2. 消融矩阵建议

1. **leave-one-out**：逐个移除模块，回答「每个模块是否必要」。
2. **组合消融**：去掉两个以上模块，检查是否存在冗余/互补。
3. **超参敏感性**：对 1-2 个关键超参做 3 档扫描（不是全网格）。
4. **数据规模缩放**：10% / 50% / 100% 数据，画 scaling 曲线。
5. **效率对照**：同效果比成本，或同成本比效果，二者选一并说明。

## 3. 预算估算表

| 实验 | 单次 GPU-h | seeds | 变体 | 小计 | 备注 |
|---|---:|---:|---:|---:|---|
| smoke | 0.5 | 1 | 2 | 1 | 管线验证 |
| baseline 复现 | 12 | 3 | 2 | 72 | gate |
| 主表 | 12 | 3 | 6 | 216 | 核心 |
| 消融 | 8 | 3 | 6 | 144 | 逐模块 |
| 效率 | 1 | 1 | 8 | 8 | 固定 batch |
| **合计** | | | | **441** | 含 30% buffer 后 ≈ 573 |

## 4. 结果表骨架（先写预期再跑）

| 方法 | 数据集 A ↑ | 数据集 B ↑ | 参数量 | 延迟 ms | 显存 GB |
|---|---|---|---|---|---|
| baseline1（引用） | 待复现 | 待复现 | | | |
| baseline2（复现） | | | | | |
| 本文 | | | | | |
EOF_EXPERIMENT_SPEC
echo "batch1 done"; ls literature-survey paper-deep-read innovation-mining experiment-design
