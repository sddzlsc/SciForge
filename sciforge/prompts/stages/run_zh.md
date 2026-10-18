# 阶段：远程训练/实验

研究主题：{{topic}}

## 上游产出
{{context}}

## 当前项目文件
{{project}}

## 你的任务

把实验真正跑起来（没有配置 GPU 服务器时，明确报告阻塞并给出配置指引）：

1. 用 `gpu_status` 检查服务器可用性（GPU 型号/显存/占用）；若未配置服务器，输出 `sciforge ssh add ...` 的配置示例并停止。
2. 用 `exp_launch`（或 `remote_sync` + `remote_run`）把代码同步到服务器并在 tmux/slurm 后台启动训练；
   长任务禁止前台阻塞，返回 job id / tmux 名称 / 日志路径。
3. 等待或轮询：用 `exp_status` / `remote_tail` 检查进程、GPU 占用、loss 是否正常（前 100 步）；
   发现 NaN/不下降/OOM 等异常时给出诊断与修复建议。
4. 训练结束后用 `exp_results` 收集 `metrics.json` 并同步回本地 `results/`。
5. 用 `write_file` 写 `run-log.md`：环境、命令、seed、耗时、最终指标、异常与处理。

## 产出要求

最终回答：运行状态（running/done/failed）、job 标识、日志路径、关键指标、异常诊断。
质量红线：不得声称「已训练」而没有真实 job 与日志；失败要保留证据（日志片段）。
