# ⑤ GPU 连接与资源确认

## 研究主题

{{topic}}

## 用户指定的服务器

{{server}}

（为空表示未指定，自动从已配置服务器中选择。）

## 任务

在不启动训练的前提下，确认后续实验可用的 GPU 资源：

1. 调用 `gpu_status` 查看所有已配置服务器的状态（GPU 型号、显存、利用率、nvidia-smi 是否可用）；
2. 若用户指定了服务器，优先用它；否则选择一个满足研究需求且当前的服务器；
3. 用 `remote_run` 做最小验证（工作目录是否可写；如方便，查看 python / torch 版本），**不要**启动训练或长时间任务；
4. 把结论写入当前目录的 `gpu-check.md`（用 `write_file`），包含：
   - 选定 server / host / workdir
   - GPU 型号、显存、当前占用
   - 可用的 conda/venv 环境（若可探测）
   - 数据/代码的上传目标目录（如 `~/sciforge/data`、`~/sciforge/code`）
   - 状态：`GPU_READY` / `GPU_NOT_READY`（附原因与修复建议）

如果没有配置服务器或 nvidia-smi 不可用：明确写出「GPU_NOT_READY」、需要用户完成的操作（GUI 服务器页/`sciforge ssh add`），不要假装有 GPU。
