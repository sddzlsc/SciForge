# SciForge 命令速查（宿主 Agent 用）

## 1. 环境与项目

```bash
sciforge doctor                       # LLM/依赖/服务器体检
sciforge init my-project --topic "..." # 新建科研项目（.sciforge/project.toml）
sciforge --help                        # 查看全部子命令
sciforge demo                          # 离线演示（Mock LLM，无需 key）
sciforge serve --port 8765             # 本地 Web 控制台
```

## 2. 文献与阅读

```bash
sciforge lit search "diffusion transformer" --source arxiv --limit 30
sciforge lit search "..." --source openalex|hf_papers|crossref|daily
sciforge lit daily --category cs.CV --limit 30
sciforge lit cite 2401.00001 -o references.bib
sciforge read 2401.00001 --note        # 下载 + 精读笔记
sciforge read 2401.00001 --ask "..."   # 就论文自由问答
```

对应工具箱工具：`lit_search`、`lit_daily`、`paper_fetch`、`paper_read`、`paper_note`、`paper_ask`、`paper_compare`、`paper_cite`。
全文只走合法来源：arXiv / Unpaywall OA / OpenAlex OA / instsci 机构访问；不访问 Sci-Hub 等未授权镜像。

## 3. 创新点

```bash
sciforge idea generate "长视频一致性" --context notes/survey.md
sciforge chat "基于这些文献，给出 3 个可证伪的创新假设"
```

## 4. 服务器与实验

```bash
sciforge ssh add gpu1 --host 1.2.3.4 --user root --key ~/.ssh/id_ed25519
sciforge ssh test gpu1
sciforge ssh sync gpu1 ./ ./sciforge --exclude data --exclude .git
sciforge gpu status gpu1
sciforge exp scaffold --template cv-classification --name exp1
sciforge exp launch --server gpu1 --script train.py --name exp1
sciforge exp status exp1
sciforge exp logs exp1 --tail 80
sciforge exp results exp1 exp2 --out results/
```

对应工具：`remote_run`、`remote_sync`、`remote_tail`、`gpu_status`、`remote_jobs`、`remote_kill`、`exp_scaffold`、`exp_launch`、`exp_status`、`exp_logs`、`exp_stop`、`exp_results`、`exp_sweep`。

## 5. 图表

```bash
sciforge fig arch --spec figures/arch.yaml -o figures/arch.svg
sciforge fig plot --spec figures/curves.yaml -o figures/curves.pdf
sciforge fig table --spec tables/main.yaml -o tables/main.tex
```

对应工具：`figure_architecture`、`figure_plot`、`figure_table`。

## 6. 写作

```bash
sciforge write draft --venue cvpr --title "..."
sciforge write section --name 03_method --content-file sections/method.tex --dir paper
sciforge write cite --arxiv 2401.00001 --dir paper
sciforge write compile --dir paper
sciforge write review --dir paper --results results/
```

对应工具：`paper_init`、`paper_section`、`citation_add`、`paper_compile`、`paper_review`。

## 7. Pipeline 与 Skill

```bash
sciforge pipeline run --recipe full --topic "..." [--mock] [--until stage]
sciforge skills list
sciforge skills search "训练 OOM"
sciforge skills show literature-survey
sciforge skills lint --all
sciforge skills install-dsh literature-survey
sciforge skills install-dsh --all
sciforge skills install https://github.com/anthropics/skills --name <skill> --target dsh
sciforge skills catalog
```

对应工具：`skill_list`、`skill_load`、`skill_search`、`skill_install`、`skill_lint`。

## 8. 什么时候用 CLI、什么时候用工具箱工具

| 场景 | 选择 |
|---|---|
| 对话中多步探索、需要根据结果决定下一步 | 工具箱工具（agent 直接调用） |
| 固定流程/批处理/CI/演示 | CLI 子命令（确定性、可脚本化） |
| 需要写入项目产物并由后续步骤读取 | CLI（产物路径固定） |
| 宿主无 SciForge 工具注册（普通 Claude/Codex） | CLI（Bash 调用） |
