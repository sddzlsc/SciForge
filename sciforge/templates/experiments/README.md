# 实验模板（T2）

`sciforge exp scaffold --template <name> --name <project>` 会把这里的模板渲染成可训练项目骨架。
渲染规则：

- `*.j2` 文件用 Jinja2 渲染后去掉 `.j2` 后缀；
- `template.yaml` 是模板元数据（默认数据集/模型），不会复制到生成的项目里；
- 其他文件原样复制（保留可执行位）。

## 内置模板

| 模板 | 用途 | 默认数据 | 默认模型 | smoke |
| --- | --- | --- | --- | --- |
| `cv-classification` | 图像分类（PyTorch ResNet/自定义 CNN） | cifar10 | resnet18 | 纯标准库，CPU 秒级 |
| `nlp-finetune` | NLP 文本分类微调（HF Trainer） | glue-sst2 | distilbert-base-uncased | 纯标准库，CPU 秒级 |
| `llm-sft` | 大模型指令微调（transformers + peft LoRA） | alpaca-style-jsonl | Qwen2.5-0.5B-Instruct | 纯标准库，CPU 秒级 |
| `generic` | 通用实验起点（numpy 训练循环） | synthetic-regression | linear | 纯标准库，CPU 秒级 |

所有模板的 `scripts/smoke.sh` 都满足「CPU + 无数据下载 + 1 分钟内跑通」；`scripts/run.sh`
会读取 SciForge 注入的 `SCIFORGE_RUN_DIR`，保证 metrics 落在 `runs/<run_id>/metrics.json`，
方便 `sciforge exp results` 自动汇总。

## 新增模板

1. 新建目录 `sciforge/templates/experiments/<name>/`；
2. 写 `template.yaml`（name/description/default_dataset/default_model/tags）；
3. 必需文件：`configs/*.yaml.j2`、`src/{data,model,train,evaluate}.py`、`scripts/run.sh`、
   `requirements.txt`、`README.md.j2`；
4. `train.py` 至少支持 `--config`、`--output-dir`、`--smoke`，并把指标写成
   `{"epochs": [{...}], "best": {...}, "final": {...}}` 的 `metrics.json`。
