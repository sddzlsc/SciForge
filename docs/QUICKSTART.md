# 10 分钟上手 SciForge

## 0. 环境

- Python ≥ 3.10（推荐 3.12）
- 可选：TeX Live（论文编译）、`rsync`/`ssh`（GPU 服务器）、`cairosvg`（图 PDF/PNG 导出）

```bash
cd sciforge
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"            # 完整：uv pip install -e ".[pdf,web,fig]"
```

## 1. 离线体验（不需要任何 key）

```bash
sciforge demo
# 产出：./sciforge-demo/{DEMO.md, .sciforge/runs/*/REPORT.md, figures/, paper/}
```

观察 `DEMO.md` 里的结构，熟悉数据落盘方式。

## 2. 初始化自己的项目

```bash
mkdir ~/research/my-first && cd ~/research/my-first
sciforge init . --name "my-first" --topic "你的研究方向"
export DEEPSEEK_API_KEY=sk-...        # https://platform.deepseek.com/
sciforge doctor
```

`doctor` 全绿即表示可以开始。LLM 未配置时所有命令都可用 `--mock` 离线跑通流程。

## 3. 第一次科研对话

```bash
sciforge chat "调研 diffusion transformer 在视频生成中的进展，总结 5 篇代表工作，并给出 3 个可做的方向"
```

Agent 会调用文献工具检索、写入 `notes/`、给出带评分的 Idea Card。

## 4. 跑一条流水线

```bash
sciforge pipeline run --recipe survey --topic "视频生成中的时空一致性"
sciforge pipeline show <run-id>
```

## 5. 配置 GPU 服务器（可选）

```bash
sciforge ssh add gpu1 --host 10.0.0.2 --user root --key ~/.ssh/id_ed25519 \
  --workdir ~/sciforge --conda-env torch
sciforge ssh test gpu1
sciforge gpu status
```

## 6. 从 idea 到实验

```bash
sciforge exp scaffold --template cv-classification --name vit-routing
sciforge exp launch --server gpu1 --script scripts/run.sh --name vit-routing-a
sciforge exp status
sciforge exp logs vit-routing-a --tail 80
sciforge exp results
```

## 7. 论文与配图

```bash
sciforge write draft --venue cvpr --title "..." --authors "A, B"
sciforge fig arch --spec figures/fig1.yaml
sciforge write review
sciforge write compile
```

## 8. 可视化 GUI（本地网页）

```bash
sciforge gui                     # 启动并自动打开浏览器：http://127.0.0.1:8765
sciforge gui --project ~/my-research --port 8765
sciforge serve --open            # 等价；不带 --open 时手动打开 URL
```

页面：对话 / 模型 / 文献 / 服务器 / 实验 / 图表 / 技能 / 关于。

**三个配置都可以在 GUI 里填空完成**（保存到 ~/.sciforge/config.toml，0600）：
- 模型：Provider 名称 / Base URL / 模型 / API Key / 角色路由 → 「保存并测试」
- 服务器：直接粘贴 `ssh -p 端口 user@host` 命令点「解析」，或手填 host/端口/用户/私钥/**密码** → 「保存并测试 SSH」
- 文献：邮箱（Unpaywall OA）/ 每日 arXiv 语言 → 「保存检索设置」
无 API key 时会自动进入离线 Mock 演示，配好 key 后同一界面直接走真实模型。

## 常见问题

| 现象 | 解决 |
|---|---|
| `缺少 API key` | `export DEEPSEEK_API_KEY=...`，或 `--mock` 离线 |
| 付费论文拿不到 PDF | 先查 arXiv/预印本/OA；配邮箱走 Unpaywall；有机构订阅可装 instsci；或手动下载合法 PDF 后 `sciforge read`（见 docs/LITERATURE.md） |
| 训练启动后没日志 | `sciforge exp status` / `sciforge remote tail ...`；tmux 会话名见 run.json |
| 图只有 SVG | macOS 默认用 `sips` 导出 PDF/PNG；Linux 可 `uv pip install cairosvg` 或 `brew install librsvg` |
| 论文无法编译 | 安装 TeX Live；`sciforge write compile` 会给出缺失宏包提示 |
