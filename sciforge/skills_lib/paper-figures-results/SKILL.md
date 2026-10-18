---
name: paper-figures-results
description: 当需要把实验结果画成论文级曲线/柱状/散点/热力图、带误差带与显著性标注时使用；产出可复现的 plot spec 与矢量图。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [figure, writing]
---

# 实验结果图（Paper Figures: Results）

## 何时使用（触发条件）

- 有 `metrics.json` / CSV / 训练日志，需要画 loss/指标曲线、主结果对比、消融图。
- 需要带误差带（mean ± std / CI）、显著性标记、参考 baselines 的多子图。
- 图要进论文（PDF 矢量、7-9pt 字号、色盲友好），而非报告截图。

## 输入 / 输出

- 输入：实验结果（多 seed 的 metrics）、要传达的结论、目标栏宽（单栏/双栏）。
- 输出：`figures/*.pdf|svg`、同目录 plot spec（YAML/JSON）、图注文本、与表格数字的核对记录。

## 工作流

1. **一张图一个结论**：先写下这句话（如「我们的方法在低数据量下优于 baseline」），再决定图型；不要把所有指标塞一张图。
2. **选图型**：趋势 → 折线 + 误差带；方法对比 → 分组柱状；相关性 → 散点 + 拟合/置信带；矩阵/ablation → 热力图；多指标权衡 → 雷达或平行坐标；分布 → 箱线/小提琴。
3. **聚合数据**：按 seed 计算 mean ± std（或 95% CI），注明 seeds 数；不要只画最好 seed；缺失点不要插值冒充数据。
4. **写 spec**：用 `figure_plot` 的 spec 定义 kind/series/labels/误差类型/显著性标记/参考线；统一 x 轴单位与对数尺度。
5. **渲染**：`figure_plot` 输出 PDF/SVG；确认无顶右边框、Okabe-Ito 配色、线宽 1-1.5、字号 7-9pt。
6. **标注**：加显著性标记（* p<0.05, ** p<0.01）、理想方向箭头（↑/↓）、baseline 参考线、必要的放大 inset。
7. **核对**：图上每个数字与 `exp_results` 生成的 Markdown 表一致；图注写清数据集、指标、seeds、评测协议。

## 质量检查清单

- [ ] 图能独立传达结论（只看图和图注就懂）。
- [ ] 误差带/误差棒存在且注明是 std 还是 CI、几个 seed。
- [ ] 坐标轴标签含单位，刻度可读；对数轴写明 log-scale。
- [ ] 颜色色盲友好、灰度打印仍可区分（线型/标记辅助）。
- [ ] 图例不遮挡数据；子图编号 (a)(b) 与正文引用一致。
- [ ] 显著性标注方法与样本量可追溯。
- [ ] 输出矢量图、字体嵌入；spec 与图同名同目录。
- [ ] 与表格数字逐项核对无冲突。

## 失败处理

| 情况 | 处理 |
|---|---|
| 缺 cairosvg/PNG 转换 | 交付 PDF/SVG；安装 cairosvg 或 `rsvg-convert` 后补 PNG |
| 曲线抖动看不清 | 平滑（注明窗口）或画原始点 + 趋势线；不要用平滑掩盖异常 |
| 多方法挤在一起 | 拆成多个子图或分面（facet），统一 y 轴便于比较 |
| seed 数不一致 | 在图中注明每个点的 seeds；不强行对齐 |
| 数据点缺失/长度不一 | 截断到公共区间并注明；不插值 |
| 审稿人说图太小 | 用双栏图或减少子图；字号不低于 7pt |
| 颜色被打印丢失 | 同时用形状/线型编码；换灰度安全配色 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 汇总实验数据 | `exp_results`（metrics.json → 表/数据摘要） |
| 渲染数据图 | `figure_plot`、`sciforge fig plot --spec figures/curves.yaml -o figures/curves.pdf` |
| 生成三线表 | `figure_table` |
| 架构图 | `figure_architecture`（见 `paper-figures-architecture`） |
| 写作引用 | `paper_section`（图号与 caption） |

## 示例

```yaml
kind: line
xlabel: "Training steps"
ylabel: "Validation accuracy (%)"
style: neurips
series:
  - {label: "Ours", x: [0, 1000, 2000, 3000], y: [52.1, 61.3, 66.0, 68.4],
     yerr: [0.8, 0.7, 0.6, 0.5], marker: o, color: "#0072B2"}
  - {label: "Baseline", x: [0, 1000, 2000, 3000], y: [50.0, 57.2, 60.1, 61.0],
     yerr: [0.6, 0.8, 0.7, 0.6], marker: s, linestyle: "--", color: "#D55E00"}
```

## 进一步阅读（渐进披露）

图型选择表、配色与显著性标注规范、常用 matplotlib 技巧见 `references/plot-recipes.md`。
