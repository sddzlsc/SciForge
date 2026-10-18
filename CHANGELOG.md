# Changelog

## v0.2.0 — 2026-10-18

自 v0.1.0 以来的主要更新：

### Pipeline 与对话
- 完整 `full` 流程重排为 13 个真实科研阶段：课题定义 → 文献调研 → 精读 → 创新点 → GPU 连接 → 数据集上传 → 实验设计 → 代码 → 训练 → 分析 → 配图 → 论文 → 自检
- 流水线移入对话：顶部按 pipeline 顺序展示阶段条，可单阶段运行、逐步展开详情
- 支持 **暂停 / 继续 / 停止**；新增单步模式（每阶段开始前暂停，点「下一步」继续）
- 刷新页面自动恢复运行中的流水线卡片；历史/重启后残留的 `running` run 可手动停止
- 新增 `POST /api/pipeline/{id}/pause|resume|stop`，`stage` / `step_mode` 参数

### 数据集与 GPU
- 数据集路径支持上传、拖拽文件/文件夹、本机目录选择（自动填绝对路径），保留目录结构
- 新增 `POST /api/datasets/upload`、`GET /api/fs/list`；上传上限 4GB / 20000 文件
- 对话内自然语言操作 GPU：上传/下载、日志、实时实验指标、下载产物

### 文献
- 新增免费源：PubMed、Europe PMC、OpenReview
- Crossref 支持出版社题录过滤（IEEE / ACM / Springer / Elsevier / Wiley / T&F / JSTOR）
- 新增全网历史会话查看/恢复

### 公式与 GUI
- 离线内置 KaTeX：`$$...$$`、`\[...\]`、`\(...\)`、`$...$` 正常渲染，失败回退 LaTeX 源码
- Agent 步数可配置：默认 30，`0` = 无上限；GUI 步数输入框 + CLI `--max-steps`
- GUI 数据集选择器、流水线对话框、历史会话、GPU 实时指标
- 新英文 README（默认）+ 中文 README；GitHub social preview 素材

### 工程
- CI 修复并在 Python 3.10 / 3.12 上全绿；测试从 341 增至 372（2 integration skipped）
- 补 `CONTRIBUTING.md`、`SECURITY.md`、`CITATION.cff`

## v0.1.0 — 2026-10-18

- 首个开源版本：文献/精读/创新点/实验/GPU/写作/配图/编排 + 本地 GUI + DSH 集成
