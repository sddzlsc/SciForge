---
name: training-debugging
description: 当训练出现 loss NaN/爆炸、OOM、跑得慢、不收敛、指标异常时使用；产出定位结论、修复补丁与回归验证方法。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [experiment, remote]
---

# 训练排障（Training Debugging）

## 何时使用（触发条件）

- loss 变 NaN/Inf、突然爆炸、长期不下降或震荡。
- CUDA OOM、训练速度异常慢、GPU 利用率低、数据加载卡住。
- 验证集指标与论文/预期差很多，怀疑实现或数据管线有问题。

## 输入 / 输出

- 输入：训练日志、config、模型/数据代码、复现命令、硬件环境。
- 输出：根因定位（数据/数值/实现/配置/硬件）、最小修复补丁、验证修复的对照实验、防回归检查项。

## 工作流

1. **先二分定位**：冻结数据只过模型（`torch.randn` 输入）→ 若仍 NaN 是模型/数值问题；若正常则回数据管线。
2. **查数值稳定性**：开 `torch.autograd.set_detect_anomaly(True)` 找第一个 NaN 算子；检查 log(0)、除零、softmax 极值、混合精度 fp16 溢出（改用 bf16 或 loss scaling）。
3. **查数据**：打印 batch 的 shape/dtype/取值 min/max/NaN 比例、标签范围；检查 padding/mask、归一化、类别越界、空样本。
4. **查学习率与优化器**：lr 太大/太小、warmup 缺失、weight decay 作用到 LayerNorm/bias、梯度裁剪缺失；打印 grad norm 曲线。
5. **查 OOM**：显存峰值 = 参数 + 梯度 + 优化器状态 + 激活；依次尝试梯度累积、AMP、梯度检查点、减 batch/序列长度、FSDP/ZeRO。
6. **做对照实验**：小数据 + 小模型 + 固定 seed 的超短 run，比较改动前后，一次只改一个变量。
7. **固化防回归**：把失败场景写成 smoke 测试（1-2 step 跑通、无 NaN、显存上限断言），进 CI。

## 质量检查清单

- [ ] 已确认第一个出现 NaN 的 step 与算子，而不是盲目调 lr。
- [ ] 数据管线有 dtype/shape/取值范围/NaN 断言。
- [ ] 混合精度方案明确（bf16 优先；fp16 需 loss scale + 溢出监控）。
- [ ] 梯度裁剪、warmup、weight decay 白名单已检查。
- [ ] OOM 修复后实测峰值显存并记录。
- [ ] 修复后有对照组与可复现命令，不只「跑通了」。
- [ ] 失败 run 的 config 与日志已保留（避免无法复盘）。

## 失败处理

| 症状 | 优先排查 |
|---|---|
| OOM | batch/seq_len、激活内存、优化器状态、是否误保留计算图（`loss.backward()` 后仍引用输出） |
| loss NaN | 数据 NaN、lr 过大、fp16 溢出、log/除零、mask 全 False、梯度爆炸 |
| loss 不降 | lr 太小、标签错位、冻结参数、数据增强过强、loss 实现错误（先过拟合 1 个 batch） |
| loss 震荡 | batch 太小、lr 无 warmup、数据顺序/采样问题、梯度裁剪阈值不当 |
| 验证指标异常好 | 数据泄漏、评测脚本用训练集、early stopping 用了验证集调参 |
| GPU 利用率低 | DataLoader workers/预取、CPU 预处理瓶颈、频繁同步、batch 太小 |
| 多卡不稳 | 随机种子/数据分片、SyncBN、梯度不同步、checkpoint 分片不一致 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 查看日志/进程 | `remote_tail`、`remote_jobs`、`remote_run` |
| GPU 状态 | `gpu_status` |
| 快速复现 run | `exp_launch`、`exp_logs` |
| 静态日志体检 | `training-debugging/scripts/diagnose_nan.py` |
| 出图看曲线 | `figure_plot`（loss/grad norm 曲线） |

## 示例

```bash
# 1) 先用脚本扫描日志里的 NaN/OOM/梯度异常
python sciforge/skills_lib/training-debugging/scripts/diagnose_nan.py runs/exp001/train.log
# 2) 探测异常算子
python -c "import torch; torch.autograd.set_detect_anomaly(True)"
# 3) 过拟合单个 batch（最有效的实现检查）
python train.py --overfit-one-batch --max-steps 50
```

## 进一步阅读（渐进披露）

OOM/NaN/不收敛的完整 playbook 与排查命令见 `references/failure-playbook.md`。
