# Contributing

感谢你愿意参与 SciForge！这是一个面向深度学习科研的本地智能体 Harness。

## 开发环境

```bash
git clone https://github.com/sddzlsc/SciForge.git sciforge
cd sciforge
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## 提交前检查

```bash
pytest -q          # 全部离线测试
ruff check sciforge tests
bash scripts/verify.sh   # 可选：含 demo / 工具 / Web 自检
```

## 约定

- 新增 Tool：放在 `sciforge/tools/`，继承 `BaseTool` 并在模块内 `register_tool(...)`；
  网络请求封装成接受可选 `httpx.Client` 的纯函数，便于离线测试。
- 新增数据源：优先只接**免费公开 API**（arXiv/OpenAlex/Crossref/PubMed/Europe PMC/OpenReview 等）；
  不接入 Sci-Hub 等未授权镜像，也不做绕过付费墙/反爬的抓取。
- 新增 Skill：`sciforge/skills_lib/<name>/SKILL.md`，遵循 ARCHITECTURE §10 的 frontmatter 规范。
- 测试默认离线；真实网络测试标 `@pytest.mark.integration`。
- UI 使用原生 HTML/CSS/JS（无构建步骤、无 CDN）。

## 安全

- 不要把 API Key、SSH 密码、个人绝对路径提交进仓库。
- 缺陷/安全问题请开 issue；敏感安全问题请私下联系维护者。
