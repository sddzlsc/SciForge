# 图型决策树与 mplstyle

## 1. 决策树

```text
图里有数值坐标轴吗？
├─ 否 → 框、箭头、层级 → 架构/流程图（figure_architecture）
└─ 是 → 想看什么关系？
   ├─ 随 x 变化的趋势 → line + error band
   ├─ 类别间大小比较 → bar / dot plot（>8 类用横向）
   ├─ 两个连续变量关系 → scatter（多时可加拟合/密度）
   ├─ 二维矩阵/消融 → heatmap
   ├─ 多指标权衡 → 气泡图/平行坐标
   ├─ 分布 → box/violin（n 小）或 hist（n 大）
   └─ 组成比例（≤4 类） → stacked bar（少用 pie）
```

## 2. mplstyle（NeurIPS 风格）

```python
import matplotlib as mpl

mpl.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial"],
    "font.size": 8,
    "axes.titlesize": 9,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "lines.linewidth": 1.2,
    "lines.markersize": 3.5,
    "legend.frameon": False,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})
```

单栏宽 3.4in，双栏 7.0in；正文中的图必须按栏宽比例缩放后仍可读。

## 3. Okabe-Ito 配色

`#0072B2 #E69F00 #009E73 #D55E00 #56B4E9 #F0E442 #CC79A7 #000000`

主方法用蓝色最粗；baseline 用灰/橙虚线；辅助信息用浅灰。

## 4. 顶会 figure checklist

- [ ] 一句话结论明确，图注可独立读懂。
- [ ] 轴标签/单位/图例完整；无中文乱码。
- [ ] 误差来源与 seeds 写明。
- [ ] 颜色 + marker/线型双重编码（灰度可读）。
- [ ] 无网格背景、无顶右边框、无 3D/阴影/渐变。
- [ ] 字体嵌入、矢量输出、文件命名与引用一致。
- [ ] 与表格数字一致；未截断坐标轴（若截断，明确标注断轴）。
- [ ] 补充材料中有大图/原始数据。

## 5. 常见审美错误

| 错误 | 修正 |
|---|---|
| 每根柱子不同颜色 | 同一系列同色，仅强调时用对比色 |
| 图例框 + 阴影 | `frameon=False` |
| 双 y 轴误导 | 尽量不用；必须用时标注清楚 |
| 过度 smooth 曲线 | 画原始点 + 平滑线并注明窗口 |
| 字号 12pt 但图缩到 1/3 | 以最终栏宽定字号 |
| 热力图无 colorbar 单位 | 加 colorbar 与标签 |
