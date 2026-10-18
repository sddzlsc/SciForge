# 阶段：代码实现

研究主题：{{topic}}

## 上游产出
{{context}}

## 当前项目文件
{{project}}

## 你的任务

按实验方案搭出可运行的训练代码骨架：

1. 用 `exp_scaffold` 生成项目骨架（按任务选 template：cv-classification / nlp-finetune / llm-sft / generic），
   或直接在现有工程上改造。
2. 实现/对接核心模块：
   - 数据管线：固定 seed、deterministic dataloader、可 smoke 的合成数据 fallback；
   - 模型：模块化拆分（backbone / 核心模块 / head），方便消融；
   - 训练脚本：`--config`、`--seed`、`--resume`、`--smoke`；
   - 评测与指标落盘 `metrics.json`。
3. **本地先跑通**：用 `shell` 跑 `--smoke`（CPU、小数据、1-2 分钟），拿到 exit code 0 与日志。
4. 写 `scripts/run.sh`（训练命令）与 `README.md`（环境、数据、复现步骤）。
5. 用 `write_file` 把实现说明写入 `code-plan.md`（改了哪些文件、如何运行、待办）。

## 产出要求

最终回答：smoke 测试结果（命令 + exit code + 关键日志）+ 文件清单 + 训练启动命令。
质量红线：不要只给「伪代码」；能本地跑的必须先跑通再交给远程。禁止硬编码数据绝对路径。
