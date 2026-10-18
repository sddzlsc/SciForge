---
name: sciforge-harness
description: 当宿主 agent（DSH/Claude/Codex）接到深度学习科研任务——文献调研、读论文、找创新点、远程 GPU 训练、写论文、画模型图——需要知道如何驱动 SciForge 时使用；产出可执行的 sciforge 命令序列与产物路径约定。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [meta, literature, experiment, writing, figure]
---

# 驱动 SciForge（SciForge Harness）

## 何时使用（触发条件）

- 用户让你做**科研**相关重活：文献调研、读论文、找创新点、跑实验、写论文、配图、做 slides。
- 你不知道该用哪个 SciForge 工具/命令，或要把任务拆成「调研→假设→实验→图→论文」流水线。
- 任务需要联网检索、SSH GPU、LaTeX 编译等宿主 agent 不擅长的确定性操作——优先交给 SciForge。

## 输入 / 输出

- 输入：用户 topic/论文 ID/实验脚本/图表需求，以及项目目录（默认当前目录）。
- 输出：命令执行结果、产物文件（`notes/`、`experiments/`、`results/`、`figures/`、`paper/`）、可续跑的状态文件。

## 工作流

1. **先体检**：`sciforge doctor` 确认 LLM/服务器/依赖状态；无 API key 时全程加 `--mock`（离线可跑）。
2. **建项目**：`sciforge init <name> --topic "..."`；生成 `.sciforge/project.toml` 与目录规范。
3. **按任务选命令**（优先 CLI，确定性、可复现）：
   - 文献：`sciforge lit search "..." --source arxiv --limit 30`、`sciforge lit daily --category cs.CV`、`sciforge read <arxiv-id> --note`
   - 每日速览：`sciforge skills show daily-arxiv-digest`
   - 创新点：`sciforge idea --topic "..." --from-library`
   - 远程 GPU：`sciforge ssh test gpu1`、`sciforge gpu status gpu1`、`sciforge exp launch --server gpu1 --script train.py`
   - 实验：`sciforge exp scaffold --template cv-classification --name exp1`、`exp status/logs/results`
   - 配图：`sciforge fig arch --spec figures/arch.yaml -o figures/arch.svg`、`sciforge fig plot --spec curves.yaml`
   - 写作：`sciforge write draft --venue cvpr`、`write section`、`write compile`、`write review`
   - 全流程：`sciforge pipeline run --recipe full --topic "..." [--mock]`
   - 演示/服务：`sciforge demo --offline`、`sciforge serve --port 8765`
4. **工具箱 vs CLI**：宿主已加载 SciForge tools（`lit_search`/`paper_read`/`remote_run`/`figure_architecture`…）时，对话内直接调用工具，适合多步交互；需要确定性产物/批处理/CI 时用 CLI 子命令。两者共享同一套配置与库。
5. **项目约定**：文献笔记 `notes/`、实验 `experiments/<hypothesis>/`、原始结果 `results/`、图 `figures/`、论文 `paper/`；跨会话续跑用 `research-state.yaml` + `findings.md` + `research-log.md`（见 `autoresearch-loop` skill）。
6. **验收**：每个阶段检查产物存在且内容完整（不是占位符）；失败按下方降级方案处理，不阻塞整条流水线。

## 质量检查清单

- [ ] 任务开始前确认项目根目录与 `.sciforge/` 配置存在。
- [ ] 联网检索失败时降级到本地库/其他源，并在结果中标注数据截止。
- [ ] 没有 API key 时用 `--mock` 验证流程，而不是报错终止。
- [ ] 长任务返回 job id/查询命令，不阻塞等待。
- [ ] 产物路径符合项目约定，可被后续 stage 找到。
- [ ] 关键命令与参数记录进 `research-log.md`，便于复现。
- [ ] 交付时给出「产物清单 + 下一步建议」。

## 失败处理

| 情况 | 处理 |
|---|---|
| 缺 LLM API key | 所有命令加 `--mock`；或配置 `DEEPSEEK_API_KEY` / OpenAI 兼容 provider |
| 没有 GPU 服务器 | 用本地 MPS/CPU 跑通小实验；`sciforge ssh add` 配置模板见 README；无服务器时 `gpu_status` 会明确提示 |
| 网络 429/超时 | 降级到其他检索源；稍后重试；用 `--mock` 继续流程 |
| LaTeX 未安装 | 只生成 tex 源与检查结果；`write compile` 给出安装/引擎提示 |
| 远程命令失败 | `remote_tail` 看日志；`gpu_status` 查资源；修好后 `exp_launch` 重跑 |
| 不知道该用哪个 skill | 先 `sciforge skills search "<任务>"` 或 `skill_search` 工具，再 `skills show` 加载工作流 |
| 产物缺失/被覆盖 | 每阶段检查 `notes/ results/ figures/ paper/`；用 run_id 隔离 |

## SciForge 工具与 CLI 对应

| 场景 | CLI | 工具箱工具 |
|---|---|---|
| 文献检索 | `sciforge lit search/daily` | `lit_search`、`lit_daily` |
| 论文精读 | `sciforge read` | `paper_read`、`paper_note`、`paper_ask` |
| 创新点 | `sciforge idea` | `sciforge.ideation` 相关工具 |
| 远程/GPU | `sciforge ssh/gpu` | `remote_run`、`gpu_status`、`remote_sync` |
| 实验 | `sciforge exp ...` | `exp_scaffold`、`exp_launch`、`exp_results` |
| 配图 | `sciforge fig ...` | `figure_architecture`、`figure_plot`、`figure_table` |
| 写作 | `sciforge write ...` | `paper_section`、`paper_cite`、`paper_compile`、`paper_review` |
| Skill 管理 | `sciforge skills list/search/install-dsh` | `skill_list`、`skill_load`、`skill_search` |

## 示例

```bash
sciforge doctor
sciforge init survey-demo --topic "长视频扩散模型"
sciforge lit search "long video diffusion" --source arxiv --limit 20
sciforge read 2401.00001 --note
sciforge idea --topic "长视频扩散模型" --from-library
sciforge exp scaffold --template cv-classification --name exp1
sciforge fig arch --spec figures/arch.yaml -o figures/arch.svg
sciforge write draft --venue cvpr
sciforge pipeline run --recipe full --topic "长视频扩散模型" --mock
```

## 进一步阅读（渐进披露）

完整命令速查（含全部子命令、工具箱工具对照表与 CLI/工具选择建议）见 `references/commands-cheatsheet.md`。
