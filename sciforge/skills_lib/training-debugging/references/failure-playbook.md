# 训练失败 playbook

## 1. OOM 排查顺序

1. `nvidia-smi` 看是否有别人的进程占卡；确认 `torch.cuda.memory_allocated()` 与 `max_memory_allocated()`。
2. 估算理论显存：参数、梯度、优化器状态（Adam = 2×参数）、激活、临时 buffer。
3. 逐项降级：
   - `batch_size` 减半 / `grad_accum` 加倍（保持等效 batch）；
   - `torch.autocast(dtype=torch.bfloat16)`；
   - `model.gradient_checkpointing_enable()`；
   - 冻结部分层 / 用 LoRA；
   - `torch.cuda.empty_cache()` 只在 debug 时用，不是解法；
   - 多卡：DDP → FSDP/ZeRO（见 `distributed-training` skill）。
4. 检查泄漏：每个 step 调用 `loss.backward()` 后是否还持有计算图（`total_loss += loss` 而不 `.item()` 是经典错误）。
5. 大数据加载 OOM：检查 pin_memory、worker 数量、缓存是否把整个数据集读进内存。

## 2. loss NaN / Inf 排查

```python
# 在训练循环里加断言（debug 模式）
assert torch.isfinite(loss), f"loss not finite at step {step}"
for name, p in model.named_parameters():
    if p.grad is not None and not torch.isfinite(p.grad).all():
        print("bad grad:", name)
        break
```

常见根因：

- 数据含 NaN/Inf（尤其经过归一化或全零方差）；
- lr 过大导致发散（先降 10× 验证）；
- fp16 溢出（换 bf16，或开 GradScaler 并检查 scale 下降频率）；
- log(0)、x/0、sqrt(0)、softmax 前未 mask（`-inf` 与全 mask）；
- attention mask 全 False 或位置编码越界；
- 自定义 loss 里用了 `.item()`/Python 除法破坏梯度。

## 3. 不收敛 / 效果差

1. **先过拟合单个 batch**：如果 50 step 后 loss 不接近 0，说明实现/lr/loss 有问题。
2. 检查标签与输入是否错位（对齐 shuffle、drop_last、padding label = -100）。
3. 检查数据增强是否过强、归一化统计量是否来自训练集。
4. 检查冻结参数、requires_grad、优化器参数列表是否漏掉模块。
5. 对比官方实现的最小差异（diff config 与 forward）。
6. 验证集指标异常好：查数据泄漏（见 `benchmark-eval-protocol` skill）。
7. 学习率扫描：在 log 尺度上试 1e-5 / 3e-5 / 1e-4 / 3e-4 / 1e-3，各跑 200 step 看 loss 曲线。

## 4. 慢 / GPU 利用率低

- DataLoader：`num_workers = 4×GPU 数` 起步，`pin_memory=True`，`persistent_workers=True`。
- 预处理放 GPU 或预先把数据 encode 成 webdataset/lmdb。
- 避免每 step `.item()`、CPU 同步、频繁日志。
- 检查是否在 CPU 上跑模型（`next(model.parameters()).device`）。
- 混合精度 + `torch.compile`（先小规模验证正确性）。
- 多卡通信瓶颈：梯度累积、bucket size、NVLink/IB 拓扑。

## 5. 防回归清单

- [ ] smoke 测试：1-2 step 前向+反向，断言 loss finite、显存 < 上限。
- [ ] 单 batch 过拟合测试（可选，慢但有效）。
- [ ] 数据管线单测：shape/dtype/范围/NaN 断言。
- [ ] 固定 seed 的指标回归基线（允许小幅波动）。
