# 可复现性打包细则

## 1. 目录结构

```text
repro/
├── README.md                 # 3 条命令跑通主表
├── env.json                  # 环境快照（collect_env.py 生成）
├── requirements.lock         # 或 uv.lock / conda env export
├── Dockerfile                # 可选，最稳
├── scripts/
│   ├── setup.sh
│   ├── prepare_data.sh
│   ├── train.sh
│   └── eval.sh
├── configs/                  # 每个表/图一个 config
├── expected_results/         # 论文数字（metrics.json + 容差）
└── LICENSE / CITATION
```

## 2. 环境锁定

- Python 版本 + 包版本精确到 patch；torch/CUDA/cuDNN 版本组合要匹配。
- 记录 `nvidia-smi`、驱动版本、GPU 型号与数量。
- 记录非 Python 依赖：ffmpeg、gcc、libGL、字体等。
- 提供 Dockerfile 时，基础镜像固定 tag（不用 `latest`）。

## 3. 数据准备

- 下载脚本必须幂等、可断点续传、带 `sha256` 校验。
- 预处理输出写 `data/processed/<version>/`，附 `manifest.json`（文件数、样本数、hash）。
- 不能公开的数据：给申请说明 + 小 toy 数据 + 预处理脚本；在 README 顶部说明。

## 4. 随机性

```python
import random
import numpy as np
import torch

seed = 42
random.seed(seed)
np.random.seed(seed)
torch.manual_seed(seed)
torch.cuda.manual_seed_all(seed)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
# DataLoader: worker_init_fn + generator，避免多 worker 重复采样
```

注意：cuDNN 卷积/AMP 仍可能有微小非确定性；在 README 说明允许容差（如 ±0.2 点）。

## 5. 结果对照

- `expected_results/main.json`：`{"metric": value}` 或表格行。
- `scripts/eval.sh` 末尾自动比对并打印 diff；超出容差返回非零退出码。
- 论文表格里的每个数字都应可定位到 `configs/<name>.yaml` + `expected_results/<name>.json`。

## 6. Reproducibility Checklist 要点（NeurIPS/ICLR 风格）

- [ ] 代码是否给出运行所需全部命令与依赖？
- [ ] 数据是否公开、可下载、有预处理说明？
- [ ] 是否报告随机种子与多次运行方差？
- [ ] 是否报告计算资源（GPU 型号/数量/时长）？
- [ ] 是否提供超参搜索范围与最终取值？
- [ ] 是否说明评测指标实现与统计显著性？
- [ ] 是否给出限制与潜在负面社会影响？
