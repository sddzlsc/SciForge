# 远程 GPU / Slurm 使用指南

## 1. 配置服务器

`~/.sciforge/config.toml`：

```toml
[[servers]]
name = "gpu1"
host = "10.0.0.2"
port = 22
user = "root"
identity_file = "~/.ssh/id_ed25519"
proxy_jump = "user@jump.example.com"   # 可选
workdir = "~/sciforge"
conda_env = "torch"
python = "python"
tmux_session = "sciforge"
scheduler = "none"                     # none | slurm
sync_excludes = ["data/", "outputs/", "wandb/", "__pycache__/", ".venv/"]
max_parallel_jobs = 2
env = { CUDA_VISIBLE_DEVICES = "0,1" }
```

或：

```bash
sciforge ssh add gpu1 --host 10.0.0.2 --user root --key ~/.ssh/id_ed25519 \
  --workdir ~/sciforge --conda-env torch
sciforge ssh test gpu1
```

前提：本机已配置好 SSH 免密登录（`ssh-copy-id` / ssh-agent），SciForge 不保存密码。

## 2. GPU 状态

```bash
sciforge gpu status                 # 所有服务器
sciforge gpu status gpu1 --watch    # 每 5s 刷新
```

解析 `nvidia-smi` 输出为表格：GPU 型号 / 显存 / 利用率 / 温度 / 进程。

## 3. 文件同步

```bash
sciforge ssh sync gpu1                      # 双向？默认本地→远端（见 --direction）
sciforge ssh sync gpu1 --direction pull     # 远端→本地
```

基于 `rsync -az`，自动排除 `sync_excludes`；无 rsync 时回退 scp。

## 4. 训练任务

```bash
# 生成训练代码骨架（含 smoke 测试）
sciforge exp scaffold --template cv-classification --name vit-routing --dataset cifar10

# 启动（tmux 后台；scheduler=slurm 时自动 sbatch）
sciforge exp launch --server gpu1 --name vit-routing-a --script scripts/run.sh --seed 42

sciforge exp status
sciforge exp logs vit-routing-a --tail 80
sciforge exp stop vit-routing-a
sciforge exp results                     # 回收 metrics.json → results/ + 三线表
```

运行记录：`<project>/runs/<run_id>/run.json`，包含 server、命令、seed、git commit、日志路径、状态、指标。
可通过 `sciforge exp status --json` 给脚本消费。

## 5. Slurm 集群

```toml
[[servers]]
name = "cluster"
host = "login.cluster.edu"
user = "me"
workdir = "/scratch/me/sciforge"
scheduler = "slurm"
partition = "gpu"
```

```bash
sciforge exp launch --server cluster --name exp1 --script scripts/run.sh --gpus 4
sciforge exp status                  # squeue / sacct
sciforge exp logs exp1
```

## 6. 故障排查

| 现象 | 排查 |
|---|---|
| ssh 超时 | `ssh -v gpu1`；检查 host/port/proxy_jump；服务器是否需 VPN |
| rsync 冲突 | `--delete` 谨慎；先用 `--dry-run` |
| 任务秒退 | `exp logs <name>` 看 stderr；检查 conda env、CUDA、数据路径 |
| OOM | 降 batch/model size；`gpu status` 看显存占用 |
| 进程丢失 | tmux `ls`；服务器重启后任务不保留，需 `--resume` 续训 |
| 找不到 conda | 配置 `conda_env`；或改用 venv 的绝对路径 `python` |
