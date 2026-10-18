# 科研工作流与 Pipeline 编排

## 三个层次

1. **Tool**：确定性能力（检索、下载、SSH、渲染、编译）。Agent 和 CLI 都调用它。
2. **Skill**：方法论（怎么做文献综述、怎么设计消融、怎么画 Figure 1）。按需加载，不占 token。
3. **Recipe / Pipeline**：把 Tool + Skill + 提示词编排成可重复的工作流，状态落盘。

## 内置 Recipe

| recipe | 阶段 | 适用 |
|---|---|---|
| `survey` | 文献调研 → 精读 → 创新点 | 探索新方向、写 related work |
| `experiment` | 实验设计 → 代码 → 远程训练 → 分析 | 已有明确想法 |
| `paper` | 结果分析 → 论文 → 配图 → 自检 | 结果已齐，冲投稿 |
| `full` | 10 阶段全流程 | 从 0 到投稿 |
| `daily` | 每日 arXiv 速览 | 跟踪领域动态 |
| `figure` | 配图专项 | 只补图表 |
| `litreview` | 多论文精读对比 | 系统综述 |

## 运行与中断

```bash
sciforge pipeline run --recipe full --topic "..." --until ideate   # 先跑到创新点
sciforge pipeline runs
sciforge pipeline show <run-id>
```

每个阶段独立目录 + 独立 session，可单独重跑：

```bash
sciforge pipeline run --recipe experiment --topic "..."   # 会在新 run 中重跑
```

## 项目级状态（双循环工作区）

借鉴 AI-research-SKILLs 的 autoresearch 模型，每个项目根目录维护：

- `research-state.yaml`：当前 run、阶段状态、下一步动作
- `findings.md`：持续演进的结论叙事（外循环）
- `research-log.md`：决策时间线（每次 run 自动追加）

实验的内循环用 `experiments/` + `runs/` + `results/` 承接；
外循环用 `findings.md` + `research-state.yaml` 承接，下一轮 pipeline 会读取上游工件。

## 阶段验收标准

| 阶段 | 完成判据 |
|---|---|
| survey | ≥8 篇真实论文、研究空白 ≥3 条、给出精读建议 |
| read | 每篇有方法拆解/证据/批判；有对比矩阵 |
| ideate | 3 个 Idea Card 带评分与 MVP；做过新颖性核查 |
| design | 假设可证伪；有参数量/计算量匹配基线；预算明确 |
| code | 本地 `--smoke` 通过（exit 0） |
| run | 有真实 job id / 日志 / 指标；异常有诊断 |
| analyze | 数字来自真实文件；有失败分析 |
| write | Abstract/Intro 每个数字可追溯；引用真实 |
| figures | 由 spec 可复现；灰度可读；caption 自解释 |
| review | Major/Minor 清单可执行；模拟审稿问题 |

## 自定义 Recipe

```python
from sciforge.pipeline.engine import Recipe, Stage

MY = Recipe(
    name="my-flow", title="我的流程", description="...",
    stages=[
        Stage(name="survey", title="调研", prompt="survey", tools=["lit_search", "write_file"]),
        Stage(name="read", title="精读", prompt="read", tools=["paper_read", "write_file"]),
    ],
)
```

然后用 Python 调用：

```python
from sciforge.config import get_settings
from sciforge.pipeline.engine import PipelineRunner

run = PipelineRunner(get_settings(), ".", mock=False).run(MY, "研究主题")
print(run.run_dir, run.status)
```
