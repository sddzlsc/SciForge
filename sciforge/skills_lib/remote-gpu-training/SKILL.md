---
name: remote-gpu-training
description: 当需要在 SSH GPU 服务器上跑训练/评测、用 tmux 或 Slurm 管理长任务、同步数据与查看 GPU 状态时使用；产出可追踪的远程 run 与日志。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [remote, experiment]
---

# 远程 GPU 训练（Remote GPU Training）

## 何时使用（触发条件）

- 本地没有 GPU 或显存不够，需要把训练/评测放到 SSH 服务器、实验室集群或 Slurm 集群。
- 任务会跑数小时以上，需要 tmux/sbatch 后台执行、断线不中断、日志可 tail。
- 需要检查 GPU 占用、清理僵尸进程、同步代码与数据。

## 输入 / 输出

- 输入：`settings.servers` 中的服务器名（host/user/key/workdir/conda_env）、训练脚本与配置、需要同步的代码/数据。
- 输出：远端 run 目录（代码 + 日志 + checkpoint）、可查询的 job id/session 名、`runs/<run_id>/run.json` 记录。

## 连接方式

- Key（推荐）：`sciforge ssh add gpu1 --host <host> --user root --key ~/.ssh/id_ed25519`
- 云 GPU 密码登录（AutoDL 等）：GUI → 服务器页粘贴 `ssh -p 端口 user@host` 点「解析」，填密码后「保存并测试 SSH」；
  或 CLI：`sciforge ssh add gpu1 --from-command "ssh -p 48322 root@host" --password "..."`（密码仅存本机 0600）
- 注意：不要把密码/实例 token 填进「SSH 私钥路径」，否则会报 `Identity file xxx not accessible`。

## 工作流

1. **先体检**：`gpu_status` 看型号/显存/利用率/占用进程；`remote_run "nvidia-smi; python -V; python -c 'import torch;print(torch.__version__)'"` 确认环境。
2. **代码同步**：`remote_sync`（rsync 优先，带 excludes：`.git`、`__pycache__`、`data/`、`wandb/`、`*.pt`）；大文件单独走一次，别每次覆盖。
3. **smoke 测试**：先用 `--smoke`/`--max-steps 20` 在远端跑通一个 mini run，确认数据路径、依赖、输出目录可写。
4. **启动长任务**：优先 `tmux new-session -d`（交互式、可 attach）或 Slurm `sbatch`（排队公平）；命令统一加 `set -e; cd <workdir>; conda run -n <env>`。
5. **记录 run 元信息**：run_id、server、命令、git commit、启动时间、日志路径、预计时长；写完再离开。
6. **监控**：`remote_tail` 看日志（先看最后 50 行 + 有没有 Traceback/OOM），`gpu_status` 看显存是否持续增长（泄漏），`exp_status` 看状态。
7. **收尾**：拉回 metrics/checkpoint；`remote_kill` 清理失败任务与残留进程；释放 GPU 前确认没有别人的进程。

## 质量检查清单

- [ ] 远端环境（CUDA/torch/依赖版本）与本地一致或已记录差异。
- [ ] 所有路径用绝对路径或相对 workdir 的稳定路径，不依赖交互式 shell 环境。
- [ ] 长任务在 tmux/slurm 中，日志有 `tee` 落盘，SSH 断开不影响。
- [ ] run.json 记录 server、命令、git hash、日志路径、状态。
- [ ] 同步 excludes 合理，避免把数据集/权重反复传输。
- [ ] 失败任务已 kill，没有占着 GPU 的僵尸进程。
- [ ] 重要 checkpoint 已回传或存到持久存储，不留在临时盘。

## 失败处理

| 情况 | 处理 |
|---|---|
| SSH 连不上 | 检查 host/port/key/proxy_jump；`remote_run "echo ok"` 最小化排查；网络问题不要反复重试卡住 |
| 断线导致任务死 | 一律用 tmux/slurm 启动；重连后 attach/tail，不要重跑 |
| 远端 OOM | 降 batch、开梯度检查点/混合精度、换更小模型；参见 `training-debugging` |
| conda 环境找不到 | 用 `conda run -n env python ...` 或 `source activate env`；把 python 绝对路径写进命令 |
| rsync 很慢/中断 | 加 `--partial --append-verify`，大文件单独同步；或用 tar 管道一次传输 |
| Slurm 一直排队 | 换 partition/降资源请求/缩短时限；先跑 smoke 验证脚本，别浪费排队时间 |
| 磁盘满 | 清理旧 checkpoint/log；输出重定向到有空间的分区 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 连通性与环境检查 | `remote_run`、`sciforge ssh test gpu1` |
| GPU 状态 | `gpu_status`、`sciforge gpu status gpu1` |
| 代码/数据同步 | `remote_sync` |
| 后台任务 | `remote_run`（tmux/nohup）、`remote_jobs`、`remote_tail` |
| Slurm | `slurm_submit`、`slurm_status`、`slurm_logs` |
| 实验生命周期 | `exp_launch`、`exp_status`、`exp_logs`、`exp_stop` |

## 示例

```bash
sciforge ssh test gpu1
sciforge gpu status gpu1
sciforge exp launch --server gpu1 --script train.py --name exp001
sciforge exp logs exp001 --tail 80
sciforge ssh sync gpu1 ./ ./sciforge --exclude data --exclude .git
```

## 进一步阅读（渐进披露）

tmux/Slurm/rsync 常用命令与远程排障清单见 `references/tmux-slurm-cheatsheet.md`。
