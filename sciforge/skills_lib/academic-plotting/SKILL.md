---
name: academic-plotting
description: 当需要为论文选择正确图型、生成 matplotlib 出版级图表或设计架构示意图时使用；产出经审美与可读性检查的矢量图。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [figure, writing]
---

# 出版级图表（Academic Plotting）

## 何时使用（触发条件）

- 有一组实验结果/数据表，需要决定用什么图、怎么排版才像顶会论文。
- 手头的图被批「像报告不像论文」：颜色刺眼、字号小、有网格背景、图例乱。
- 需要把「文字描述的方法」转成架构/流程示意图。

## 输入 / 输出

- 输入：数据（CSV/JSON/metrics）、要传达的一句话结论、目标栏宽（单栏/双栏）。
- 输出：`figures/*.pdf|svg`、plot spec（YAML/JSON）、caption 文本、可复现渲染命令。

## 工作流

1. **先定图型再动代码**：数值轴 → 数据图（line/bar/scatter/heatmap）；框图/箭头 → 架构图（`figure_architecture`）；不要用 AI 生图替代数值图。
2. **一句一图**：先写下这张图要证明的结论；如果一个图有两个结论，拆图。
3. **数据整理**：多 seed 聚合为 mean±std/CI；统一单位与小数位；清洗异常点并记录。
4. **写 spec**：用 `figure_plot` 的 YAML spec 定义 kind/series/xy 标签/图例/误差；同一论文统一 style（neurips）。
5. **渲染**：矢量导出；字号 7-9pt、无顶右边框、Okabe-Ito、线宽 1-1.5、`bbox_inches="tight"`。
6. **审美检查**：缩放到栏宽看可读性；灰度打印可区分；去掉网格/阴影/3D/渐变；图例不压数据。
7. **Caption 与引用**：图注写清数据、指标、seeds、误差含义、↑↓方向；与正文编号一致。

## 质量检查清单

- [ ] 图型与数据/结论匹配（趋势不画柱状、比较不画饼图）。
- [ ] 误差带/误差棒存在且注明 std/CI 与 seeds。
- [ ] 坐标轴标签含单位；刻度不过密；对数轴标注。
- [ ] 字号 ≥7pt，缩放到栏宽仍可读；配色色盲友好。
- [ ] 无多余装饰（网格、背景色、3D、阴影）。
- [ ] 图例位置不遮挡数据；子图编号与正文一致。
- [ ] 矢量输出（PDF/SVG），字体嵌入；spec 落盘可复现。
- [ ] 与表格/正文数字一致。

## 失败处理

| 情况 | 处理 |
|---|---|
| 没有 cairosvg/PNG 需求 | 交付 PDF/SVG；安装 cairosvg 或 rsvg-convert 后再补 PNG |
| 数据点太多成糊状 | 降采样+误差带；或按类别分面 |
| 颜色区分度差 | 限制 6 色内；加 marker/线型；灰度安全 |
| 文字重叠 | 缩短标签/旋转刻度/加大画布；图例移出绘图区 |
| 图在栏宽下太小 | 改双栏图、减子图数量、拆成两图 |
| 审美不符顶会 | 套 neurips/cvpr mplstyle；去掉网格与顶右边框；统一字号 |
| 图与结论不符 | 回到数据核对；不要为了好看挑 seed/截断坐标轴（截断需标注） |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 数据图 | `figure_plot`、`sciforge fig plot --spec curves.yaml -o figures/curves.pdf` |
| 架构图 | `figure_architecture`、`sciforge fig arch` |
| 表格 | `figure_table`、`sciforge fig table` |
| 数据来源 | `exp_results` |
| 写作衔接 | `paper_section`（caption 与引用） |

## 示例

```yaml
kind: bar
xlabel: "Method"
ylabel: "FVD ↓"
style: neurips
series:
  - {label: "FVD", x: [Baseline, Ours], y: [48.7, 41.2], yerr: [1.1, 0.9], color: "#D55E00"}
```

## 进一步阅读（渐进披露）

图型决策树、mplstyle 参数与顶会 figure checklist 见 `references/chart-chooser.md`，数据图细节另见 `paper-figures-results`。
