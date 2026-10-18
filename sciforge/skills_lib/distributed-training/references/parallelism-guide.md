# 并行策略选择与调优

## 1. 显存估算（bf16 训练）

| 组成 | 近似大小 |
|---|---|
| 参数 | 2 bytes × P |
| 梯度 | 2 bytes × P |
| 优化器状态（Adam） | 8-12 bytes × P（fp32 动量+方差） |
| 激活 | 与层数、batch、序列长度、hidden 成正比（可用 checkpoint 换时间） |
| 临时 buffer | 1-2 GB / 卡（依算子而定） |

示例：7B 模型全量微调约需 14GB（参数+梯度）+ 70-84GB（Adam fp32）+ 激活 ≈ 单卡放不下 → FSDP/ZeRO-3。

## 2. 策略选择矩阵

| 情况 | 推荐 | 备注 |
|---|---|---|
| 模型 + 优化器 + 激活都能放单卡 | DDP | 最简单，通信量小 |
| 参数/优化器放不下，单层放得下 | FSDP / ZeRO-2/3 | 分片；DCP 保存 |
| 单层也放不下 | TP + PP（Megatron/DeepSpeed） | 复杂，优先用成熟框架 |
| 超长序列 | 序列并行 + 激活检查点 + FlashAttention | 注意 attention 显存 |
| 多机 | 节点内 NVLink、跨节点 IB；检查带宽 | 通信占比 >30% 需优化 |
| 微调大模型 | LoRA/QLoRA + DDP | 最省显存 |

## 3. 启动与对齐

- `torchrun --standalone --nproc_per_node=N` 单机；多机加 `--nnodes/--rdzv_endpoint`。
- 每进程：`torch.cuda.set_device(int(os.environ["LOCAL_RANK"]))`。
- 等效 batch：`per_device_batch × world_size × grad_accum`，与单卡 baseline 保持一致再对比。
- DDP：`model = DistributedDataParallel(model, device_ids=[local_rank])`；只 rank0 保存/打印。
- FSDP2：`fully_shard` 自底向上，先 shard 子模块再 root；用 `model(input)` 而不是 `model.forward(input)`。

## 4. 学习率与 batch 放大

- 线性缩放：`lr_new = lr_base × global_batch_new / global_batch_base`（配合 warmup）。
- 平方根缩放适用于部分任务；以实际收敛曲线为准。
- 大 batch 训练建议：更长 warmup、LAMB/Adafactor 等自适应优化器（视任务）。

## 5. 通信优化

- 梯度累积替代超大 batch（减少每 step 同步）。
- bucket size / `gradient_as_bucket_view` 调优。
- 用 `NCCL_DEBUG=INFO`、`nvidia-smi topo -m` 检查拓扑。
- 避免每 step 做全局 barrier/日志同步；rank0 聚合。

## 6. 常见坑

| 坑 | 现象 | 修复 |
|---|---|---|
| 数据分片重复 | rank 间样本重复 | DistributedSampler + set_epoch |
| 未同步 BN | 多卡指标差 | SyncBatchNorm / LayerNorm |
| 只 rank0 保存但含分片 | 加载失败 | DCP/全量 state_dict 汇总 |
| 评估在每个 rank 跑 | 卡顿/日志混乱 | 只在 rank0 或 all-gather 后评 |
| dataloader worker 过多 | CPU 争抢 | workers = 4×GPU 数为上限 |
| 随机种子相同 | 各 rank 同步 dropout | 按 rank/epoch 设 seed |
