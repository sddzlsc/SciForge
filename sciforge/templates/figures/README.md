# SciForge 顶会级配图模板（T3）

本目录包含**声明式**图表模板与示例产物，可直接复制修改后用于论文。

## 目录

```
templates/figures/
├── transformer_block.yaml        # 纵向 block：Pre-LN Transformer（含残差旁路）
├── multimodal_pipeline.yaml      # 横向 pipeline：CLIP 风格多模态对比学习
├── training_loop.yaml            # 横向 pipeline：训练循环（带回环箭头）
├── plot_line.yaml                # matplotlib：带误差带的收敛曲线 + 显著性星标
├── plot_bar.yaml                 # matplotlib：消融柱状图
├── plot_heatmap.yaml             # matplotlib：混淆矩阵
├── plot_radar.yaml               # matplotlib：雷达对比图
├── table_main_results.yaml       # booktabs：多数据集列分组主结果表
├── table_ablation.yaml           # booktabs：消融表（含行分组）
└── outputs/                      # 实际渲染产物（可直接打开验收）
    ├── fig1_architecture.svg/.png   # 架构图（PDF 需 cairosvg 等转换器）
    ├── fig2_curves.pdf/.svg/.png    # 数据图（matplotlib，PDF 字体已嵌入）
    ├── fig3_transformer_block.svg/.png
    ├── table_main_results.tex/.md
    └── *.spec.yaml                  # 与成品同目录的 spec（保证可复现）
```

## 用法

```bash
# 架构图：输出 SVG，同时尝试 PDF/PNG；缺转换器时自动降级并在结果里说明
sciforge fig arch --spec sciforge/templates/figures/multimodal_pipeline.yaml -o figures/fig1_arch.svg

# 数据图：matplotlib，NeurIPS/CVPR 风格
sciforge fig plot --spec sciforge/templates/figures/plot_line.yaml -o figures/fig2_curves.pdf

# 三线表：最佳加粗、次佳下划线、多数据集 \multicolumn + \cmidrule
sciforge fig table --spec sciforge/templates/figures/table_main_results.yaml -o tables/table_main.tex

# 重新写出全部模板
sciforge fig templates --dest figures/templates
```

Python API（`docs/ARCHITECTURE.md` §11/§14.3 契约）：

```python
from sciforge.figures.arch import render_architecture          # -> Path
from sciforge.figures.plots import render_plot                # -> Path
from sciforge.figures.tables import render_latex_table        # -> str
```

## 设计规范（为什么“像论文图”）

- 纯白底、无渐变/阴影；正文统一 8pt（子标签 7pt、注释 6.5pt）；
- Okabe-Ito 色盲友好配色；关键模块主色 `#0072B2` 强调；
- 网格对齐、圆角模块、0.9pt（≈1.2px）细箭头；分组浅色底 + 虚线边框；
- 横向 pipeline 与纵向 block 两种走向，tensor 用扁条、note 用小字；
- 数据图：无顶/右边框、tick 朝内、7–9pt、`bbox_inches="tight"`、
  PDF `fonttype=42`（可嵌入，符合多数会议投稿要求）；
- 每个成品旁都有 `.spec.yaml`，改数据即可复现。
