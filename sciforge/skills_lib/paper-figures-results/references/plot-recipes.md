# 结果图图型选择与排版规范

## 1. 图型选择表

| 想表达 | 首选 | 备选 | 避免 |
|---|---|---|---|
| 随训练/规模变化的趋势 | 折线 + 误差带 | 散点 + 拟合 | 柱状图堆叠多个时间点 |
| 多方法在多个数据集上的对比 | 分组柱状 / 点图（dot plot） | 表格 | 3D 柱状 |
| 两变量相关性 | 散点 + 拟合线/CI 带 | 六边形分箱图 | 过拟合曲线不加说明 |
| 消融矩阵 | 热力图 / 分组柱状 | 表格 | 雷达图（>3 维时难读） |
| 多指标权衡（精度-延迟-显存） | 气泡图 / 平行坐标 | 表格 | 一张图塞 6 种颜色 |
| 分布差异 | 箱线图 / 小提琴图 | 直方图 | 只画均值 |
| 数据集构成/比例 | 堆叠条 | 表格 | 饼图（>4 类） |

## 2. 配色（Okabe-Ito 色盲友好）

| 名称 | HEX |
|---|---|
| Blue | #0072B2 |
| Orange | #E69F00 |
| Green | #009E73 |
| Vermillion | #D55E00 |
| Sky blue | #56B4E9 |
| Yellow | #F0E442 |
| Purple | #CC79A7 |
| Black | #000000 |

规则：主方法用 Blue 且最粗；baseline 用灰/黑虚线；最多 6 色，超过则分面；同时用 marker/line style 区分。

## 3. 排版参数（NeurIPS/CVPR 风格）

```python
import matplotlib as mpl
mpl.rcParams.update({
    "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 7,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.spines.top": False, "axes.spines.right": False,
    "lines.linewidth": 1.2, "lines.markersize": 4,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
    "pdf.fonttype": 42,  # 嵌入字体
})
```

单栏宽 ≈ 3.4 in，双栏 ≈ 7.0 in；导出 PDF/SVG，PNG 仅用于预览。

## 4. 误差带与显著性

- 多 seed：画 `mean ± std` 阴影带（`fill_between`）或误差棒，图注写 $\pm$ std over N seeds。
- 显著性：配对检验后标 `*`/`**`；不要只标星不写检验方法。
- 参考线：baseline 常量用灰色虚线并标注数值。
- 理想方向：y 轴标签后加 ↑/↓；对数轴标注 `log-scale`。

## 5. 自检命令

```bash
sciforge fig plot --spec figures/curves.yaml -o figures/curves.pdf
# 渲染后：
# 1) 缩放到栏宽看字号；2) 灰度打印看区分度；3) 检查图例是否遮挡数据
```
