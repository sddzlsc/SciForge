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
# Key 登录
sciforge ssh add gpu1 --host 10.0.0.2 --user root --key ~/.ssh/id_ed25519 \
  --workdir ~/sciforge --conda-env torch

# 粘贴云厂商给的 ssh 命令 + 密码（AutoDL 等）
sciforge ssh add autodl --from-command "ssh -p 48322 root@connect.bjb2.seetacloud.com" \
  --password "你的密码"

sciforge ssh test gpu1
```

前提：推荐本机已配置 SSH 免密登录（`ssh-copy-id` / ssh-agent）。也支持密码登录（见 §2.5），
密码只存本机 `~/.sciforge/config.toml`（0600）。

## 2. GPU 状态

```bash
sciforge gpu status                 # 所有服务器
sciforge gpu status gpu1 --watch    # 每 5s 刷新
```

解析 `nvidia-smi` 输出为表格：GPU 型号 / 显存 / 利用率 / 温度 / 进程。

## 2.5 密码登录 / 粘贴 SSH 命令（AutoDL 等）

很多云 GPU（如 AutoDL）只给一条带端口的登录命令和密码，可直接在 GUI 里粘贴：

```text
ssh -p 48322 root@connect.bjb2.seetacloud.com
```

1. 打开 GUI → **服务器** 页；
2. 在「粘贴 SSH 登录命令」填入上面的命令，点 **解析**（自动填 host / port / user）；
3. 在「登录密码」填入密码（只保存在本机 `~/.sciforge/config.toml`，0600，不会回传浏览器）；
4. 点 **保存并测试 SSH**。

实现说明：密码登录使用 OpenSSH 的 `SSH_ASKPASS` 机制，**不需要安装 sshpass**；
密码不会出现在命令行参数里（只通过子进程环境变量传给 askpass 脚本，脚本本身不含密码）。

> 安全建议：密码是明文保存在本机配置文件里的。长期使用建议改用 SSH Key：
> `ssh-keygen -t ed25519` + `ssh-copy-id -p 48322 root@host`，然后把「SSH 私钥路径」填成
> `~/.ssh/id_ed25519`，密码留空。

### 常见错误

| 错误 | 原因 / 处理 |
|---|---|
| `Identity file xxx not accessible` | 把密码/AutoDL token 误填进了「SSH 私钥路径」；清空该字段，改用「登录密码」 |
| `Permission denied (publickey,password)` | 密码错误，或既填了无效私钥又没填密码；清空私钥、填写密码 |
| `Connection timed out` | 端口/地址错误，或需要先连 VPN；检查 `-p` 端口与 host |
| 想换回 Key 登录 | 清除密码（勾选「清除已存密码」），私钥路径填对，保存并测试 |

## 2.6 在对话里操作 GPU（上传 / 下载 / 实时指标）

对话页的 Agent 已接入远程工具，用自然语言即可操作，例如：

```text
查看 GPU 状态，告诉我显存和利用率
把 uploads/ 里的实验代码传到 GPU，然后启动训练
把远端 runs/exp1/metrics.json 和 train.log 下载回来
看一下现在最新实验的指标，loss 趋势正常吗？要不要停？
tail 一下远端训练日志最后 50 行
```

对应工具：

| 需求 | 工具 | 说明 |
|---|---|---|
| 看 GPU | `gpu_status` | nvidia-smi 解析；无服务器时本机 MPS 降级 |
| 上传 / 下载 | `remote_sync` | `direction=upload/download`；下载结果会出现在工具卡「⬇ 下载」 |
| 看实时指标 | `remote_metrics` | 默认自动发现最新 `runs/*/metrics.json`，返回最新值 + 历史序列 |
| 看日志 | `remote_tail` | tail 远端日志 |
| 任务状态 | `exp_status` / `exp_jobs` | tmux / Slurm 状态 |
| 提交 Slurm | `slurm_submit/status/logs` | 集群环境 |

另外「实验」页新增 **GPU 实时指标** 面板：选服务器 + 指标路径（留空自动发现），
支持每 5 秒自动刷新，直接画 loss/准确率曲线。

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
