# 阶段：论文写作

研究主题：{{topic}}

## 上游产出
{{context}}

## 当前项目文件
{{project}}

## 你的任务

基于真实实验与文献，写出可投稿的论文骨架（不要写空话）：

1. 用 `paper_init` 初始化 LaTeX 工程（venue 取配置，缺省 cvpr；没有 latex 环境也要生成完整 .tex）。
2. 按叙事线写各节：
   - Title / Abstract：问题 → 洞察 → 方法 → 关键数字（数字必须来自上游结果）；
   - Introduction：3 个不足（引用真实文献）→ 我们的洞察 → 贡献列表；
   - Related Work：2-3 条路线 + 与本文差异；
   - Method：问题定义、框架（配 Figure 1）、核心模块（公式 + 直觉）、训练目标；
   - Experiments：设置、主结果、消融、可视化、失败分析；
   - Limitations / Conclusion。
3. 引用：只用上游真实检索到的论文，用 `paper_cite` 生成 BibTeX；禁止编造引用。
4. 用 `paper_review` 或 `check` 检查 TODO/占位符/未定义引用。
5. 关键草稿同步写入 `paper-draft.md`（便于非 LaTeX 用户阅读）。

## 产出要求

最终回答：论文故事线（一句话）+ 各节摘要 + 引用列表 + 待补实验清单 + 编译状态。
质量红线：Abstract/Intro 的每个数字可追溯到结果文件；不夸大 claims。
