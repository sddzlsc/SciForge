---
name: reproducibility-package
description: 当需要整理可复现的代码/数据/环境/实验说明、通过 reproducibility checklist 或开源发布时使用；产出可一键复跑的 artifact 包。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [reproducibility, meta]
---

# 可复现性打包（Reproducibility Package）

## 何时使用（触发条件）

- 论文要提交 artifact/reproducibility checklist，或审稿人要求提供复现材料。
- 开源代码前需要整理环境、数据准备、训练命令与结果对照。
- 团队交接/换机器后要能一键复跑实验。

## 输入 / 输出

- 输入：代码仓库、环境（conda/pip/uv）、数据获取方式、实验 configs、预期指标、硬件信息。
- 输出：`repro/` 包（README、env.lock、scripts/、configs/、expected_results/）+ `repro-checklist.md`。

## 工作流

1. **锁定环境**：导出 `python --version`、`pip freeze`/`uv lock`、CUDA/驱动、关键库版本；记录硬件（GPU 型号/数量/显存）。
2. **数据可获取**：给出下载链接/脚本、校验和、预处理命令；不能公开的数据说明申请方式与替代小样本。
3. **一键脚本**：提供 `scripts/setup.sh`、`scripts/prepare_data.sh`、`scripts/train.sh`、`scripts/eval.sh`，全部无交互、可幂等重跑。
4. **配置即实验**：所有超参进 config 文件，命令行可覆盖；论文每个表格对应一个 config 路径。
5. **固定随机性**：设置 seed（Python/NumPy/Torch）、确定性算法、DataLoader worker seed；说明哪些操作仍不可完全确定。
6. **结果对照**：提供 `expected_results/`（论文表格对应的 metrics.json）与容差；跑通后自动 diff。
7. **资源与时长**：标注单次训练 GPU 型号/时长/显存峰值，让复现者能估预算。
8. **打包发布**：README 首屏给「3 条命令复现主表」；清理大文件/密钥/内部路径；加 LICENSE。

## 质量检查清单

- [ ] 全新机器按 README 三步能跑通 smoke（≤10 分钟）。
- [ ] 环境版本精确（含 CUDA/torch 匹配），无隐式系统依赖。
- [ ] 数据准备可无交互完成，含校验和/样本数检查。
- [ ] 论文每个表/图都能映射到 config + 命令。
- [ ] 随机种子与确定性设置完整，并说明残余差异。
- [ ] expected_results 与论文数字一致，容差范围明确。
- [ ] 无绝对路径、无个人 API key、无内部数据路径。
- [ ] 许可证/引用信息齐全；大文件走外部存储链接。

## 失败处理

| 情况 | 处理 |
|---|---|
| 环境无法完全复现 | 提供 Dockerfile/conda env 文件；记录差异与已知影响 |
| 数据不能公开 | 提供下载申请说明 + 小规模 toy 数据跑通流程 + 校验脚本 |
| 结果差异大 | 检查 seed/数据划分/依赖版本/硬件（尤其 AMP、cuDNN 非确定性） |
| 复现耗时过长 | 提供缩短版 config（小数据/少步数）用于验证管线，并说明与全量差异 |
| 脚本依赖交互 | 改为环境变量/参数传入；禁止 `input()` |
| 仓库太大 | 用 git-lfs 或外部存储；README 给出下载脚本与校验和 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 环境收集 | `reproducibility-package/scripts/collect_env.py` |
| 实验骨架 | `exp_scaffold`、`sciforge exp scaffold` |
| 启动与结果 | `exp_launch`、`exp_results`、`exp_status` |
| 项目初始化 | `sciforge init my-project --topic "..."` |
| 产物整理 | `figure_table`、`paper_section`（附录复现说明） |

## 示例

```bash
# 1) 收集环境
python sciforge/skills_lib/reproducibility-package/scripts/collect_env.py -o repro/env.json
# 2) 验证 smoke
bash scripts/setup.sh && bash scripts/train.sh --config configs/smoke.yaml
# 3) 主表复现
bash scripts/train.sh --config configs/main.yaml && bash scripts/eval.sh
```

## 进一步阅读（渐进披露）

reproducibility checklist、env 锁定与打包结构见 `references/repro-checklist.md`。
