---
name: distributed-training
description: 当单卡放不下模型/需要多卡多机训练、要选 DDP/FSDP/ZeRO/TP 策略或排查多卡不收敛时使用；产出可运行的并行训练配置与调优检查表。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [experiment, remote]
---

# 分布式训练（Distributed Training）

## 何时使用（触发条件）

- 模型/数据规模超出单卡显存，需要 DDP/FSDP/ZeRO/TP 等并行方案。
- 多卡训练吞吐不升反降、loss 与单卡对不上、checkpoint 无法加载。
- 要在 8 卡以上或跨节点训练，需要选策略与估算通信开销。

## 输入 / 输出

- 输入：模型参数量、序列长度、batch 目标、GPU 数量与互联（NVLink/IB/以太网）、训练代码。
- 输出：并行策略选择、启动命令（torchrun/sbatch）、显存与吞吐预估、正确性对齐记录。

## 工作流

1. **估算需求**：参数、梯度、优化器状态、激活显存；确定单卡能否装下（Adam 约 6-8× 参数量字节数 + 激活）。
2. **选策略**（由简到繁）：
   - 单卡放得下 → DDP（数据并行）；
   - 放不下但单层放得下 → FSDP/ZeRO-2/3（分片参数/梯度/优化器状态）；
   - 单层放不下 → TP/流水线并行（Megatron/DeepSpeed）；
   - 长序列 → 序列并行 + 激活检查点；
   - 多机 → 检查网络拓扑，优先 NVLink 内节点、IB 跨节点。
3. **启动**：统一用 `torchrun --nproc_per_node=N` 或 Slurm `srun`/`sbatch`；每个进程按 `LOCAL_RANK` 设 device。
4. **正确性对齐**：小模型/小数据先跑 100 step，与单卡（等效 batch）对齐 loss 曲线；不一致先查数据分片、梯度同步、随机种子。
5. **调优**：batch 随卡数线性放大时同步放大 lr（或 warmup）；开 AMP（bf16 优先）；通信瓶颈时调 bucket size/梯度累积。
6. **Checkpoint**：分布式分片保存（DCP/FSDP state_dict）；加载时保证并行度可不同；定期保存并验证可恢复。
7. **监控**：吞吐（samples/s）、GPU 利用率、通信时间占比、显存峰值；记录到 run.json。

## 质量检查清单

- [ ] 并行策略与显存估算匹配，没有「先跑再看」。
- [ ] 启动命令固定（torchrun/sbatch），可通过 `exp_launch` 复现。
- [ ] 等效 global batch 与单卡 baseline 一致或明确说明。
- [ ] 与单卡 loss 曲线对齐（前 100-500 step）。
- [ ] 数据分片无重复/遗漏；shuffle 与 seed 设置正确。
- [ ] checkpoint 分片可保存与恢复（含优化器状态）。
- [ ] 记录 GPU 数量、拓扑、每卡显存与吞吐。
- [ ] 多机时检查 hostfile/rendezvous 与防火墙端口。

## 失败处理

| 情况 | 处理 |
|---|---|
| NCCL 通信超时/挂起 | 检查端口/防火墙/IB；设 `NCCL_DEBUG=INFO`；减少节点逐步定位 |
| 多卡 loss 与单卡不一致 | 查数据分片重复、梯度平均（除以 world_size）、SyncBN、种子 |
| 吞吐随卡数不升 | 通信瓶颈/小 batch/CPU 瓶颈；加大每卡 batch、梯度累积、优化 bucket |
| OOM 只在 rank0 | 检查主进程额外内存（日志/评估/保存全量 state_dict） |
| checkpoint 加载失败 | 保存/加载并行度不一致；用 DCP 或 `state_dict` 转换 |
| 多机连不上 | 检查 rendezvous endpoint、SSH 免密、节点时钟同步 |
| AMP 下多卡发散 | fp16 溢出；换 bf16 或调 loss scale；检查梯度裁剪 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 生成多卡启动脚本 | `exp_scaffold`、`sciforge exp scaffold` |
| 远程启动 | `exp_launch`、`remote_run`、Slurm `slurm_submit` |
| GPU/拓扑检查 | `gpu_status`、`remote_run "nvidia-smi topo -m"` |
| 日志与指标 | `exp_logs`、`exp_results`、`figure_plot` |
| 参考 | `remote-gpu-training` skill（tmux/slurm/rsync） |

## 示例

```bash
# 8 卡单机 DDP
torchrun --standalone --nproc_per_node=8 train.py --config configs/ddp8.yaml
# FSDP/ZeRO 示例（以 accelerate 配置为准）
accelerate launch --config_file configs/fsdp.yaml train.py
# 多机 Slurm
srun --nodes=2 --ntasks-per-node=8 torchrun --nnodes=2 --nproc_per_node=8 \
  --rdzv_endpoint=$MASTER_ADDR:29500 train.py --config configs/fsdp16.yaml
```

## 进一步阅读（渐进披露）

DDP/FSDP/ZeRO/TP 的选择矩阵、显存估算与调优参数见 `references/parallelism-guide.md`。
