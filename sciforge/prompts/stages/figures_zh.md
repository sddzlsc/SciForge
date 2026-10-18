# 阶段：顶会级配图

研究主题：{{topic}}

## 上游产出
{{context}}

## 当前项目文件
{{project}}

## 你的任务

为目标论文准备 **Figure 1 模型总览图 + 实验曲线 + 三线表**：

1. **Figure 1（模型图）**：用 `figure_architecture` 渲染。先写 YAML spec
   （nodes: 数据/backbone/核心模块/loss/输出；groups 分组；edges 数据流；kind 用 tensor/module/op/loss/data/note），
   要求：横向或纵向数据流清晰、核心模块视觉强调、8pt 字号、色盲友好配色、无多余装饰。
   输出到 `figures/fig1.svg`（有 cairosvg 时同时出 pdf/png）。
2. **实验曲线**：从 `results/plotdata.json` 或 analysis 表格取真实数据，用 `figure_plot`
   生成 `figures/curves.pdf`（NeurIPS 风格，带误差带、无顶右边框）。
3. **三线表**：用 `figure_table` 生成主结果/消融表 LaTeX 片段，保存到 `figures/tables.tex`。
4. 自检：灰度可读性、字号、单位、图例、子图编号、caption 是否自解释。
5. 用 `write_file` 写 `figures.md`（每个图的 spec 路径、输出路径、caption 草稿）。

## 产出要求

最终回答：图清单（文件名/类型/spec 路径）+ caption 草稿 + 自检结论。
质量红线：所有图必须由 spec 可复现；禁止用占位示意图冒充结果图。
