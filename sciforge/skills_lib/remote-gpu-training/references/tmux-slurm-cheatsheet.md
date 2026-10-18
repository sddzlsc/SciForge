# 远程执行速查表

## 1. tmux 长任务

```bash
# 新会话跑训练（断线不中断）
tmux new-session -d -s exp001 -c ~/sciforge \
  "conda run -n torch python train.py --config configs/base.yaml 2>&1 | tee runs/exp001/train.log"

tmux ls                         # 列出会话
tmux attach -t exp001           # 进入查看（Ctrl-b d 退出不杀任务）
tmux kill-session -t exp001     # 停止
```

nohup 兜底（无法用 tmux 时）：

```bash
nohup bash -c 'cd ~/sciforge && python train.py' > runs/exp001/train.log 2>&1 &
echo $! > runs/exp001/pid
```

## 2. Slurm

```bash
# sbatch 模板
#!/bin/bash
#SBATCH --job-name=exp001
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --output=runs/%x/%j.out
#SBATCH --error=runs/%x/%j.err
set -e
cd ~/sciforge
conda run -n torch python train.py --config configs/base.yaml
```

```bash
sbatch run.sbatch
squeue -u $USER
sacct -j <jobid> --format=JobID,State,Elapsed,MaxRSS
scancel <jobid>
```

## 3. rsync 同步

```bash
rsync -avz --partial --exclude .git --exclude __pycache__ --exclude data/ \
  ./ gpu1:~/sciforge/
# 拉回结果
rsync -avz gpu1:~/sciforge/runs/exp001/ ./runs/exp001/
```

## 4. 远程排障顺序

1. `nvidia-smi` —— 卡在不在、显存是否被占。
2. `df -h` —— 磁盘是否满（训练写 checkpoint 常见）。
3. `free -g` —— 内存是否够（数据加载进程）。
4. `ps aux | grep train` —— 是否有重复进程/僵尸。
5. `tail -n 100 *.log` —— Traceback / OOM / loss NaN。
6. `python -c "import torch; print(torch.cuda.is_available())"` —— 驱动/CUDA 是否正常。
