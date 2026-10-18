---
name: paper-figures-architecture
description: 当需要画论文 Figure 1 模型架构图、系统流程图或训练管线图时使用；产出声明式 spec 与顶会级 SVG/PDF 架构图。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [figure, writing]
---

# 论文架构图（Paper Figures: Architecture）

## 何时使用（触发条件）

- 论文需要 Figure 1（方法总览）、系统架构图、数据流/训练流程图、模块细节图。
- 已有方法描述或代码，需要转成审稿人一眼能读懂的图。
- 图需要可复现（spec 落盘）且风格达到 CVPR/NeurIPS/ICLR 水准。

## 输入 / 输出

- 输入：方法描述 / 论文 Method 草稿 / 代码模块划分；可选已有手绘草图。
- 输出：`figures/fig1_arch.svg`（+ PDF/PNG）、同目录 `fig1_arch.yaml` spec、图注文本（caption）。

## 工作流

1. **先定信息层级**：一张图只讲一个故事；核心贡献模块放中间/高亮，输入输出放两侧，损失/目标放底部。
2. **提炼节点**：从 Method 中抽 6-12 个节点（embedding、encoder block、fusion、decoder、loss 等），把细节模块合并成小组（group），避免超过 15 个节点。
3. **写 spec**：用 `figure_architecture` 的 DSL 定义 `nodes`（id/label/sub/kind/group/row/col）、`edges`（含 label/style）、`groups`；`kind` 取 `tensor|module|op|loss|data|note`。
4. **粗排后对齐**：先按行分层（数据→表示→模块→输出→loss），再在同层内按列排；减少跨层长边与交叉。
5. **渲染检查**：`figure_architecture` 输出 SVG 后检查文字截断、箭头压字、分组框越界；PNG/PDF 依赖缺失时先交付 SVG。
6. **统一术语**：图中模块名必须与正文 Method 完全一致（大小写、缩写），图注解释每个缩写和箭头含义。
7. **版本化**：spec 与图同目录提交 git；改图先改 spec，禁止手改 SVG。

## 质量检查清单

- [ ] 不看正文也能在图注帮助下理解方法主流程。
- [ ] 节点 ≤15，主次分明；核心创新模块有视觉强调（颜色/边框/分组）。
- [ ] 箭头方向一致、无交叉压字；数据流从左到右或从上到下单向。
- [ ] 配色色盲友好（Okabe-Ito），白底，无渐变/阴影等装饰。
- [ ] 字号 ≥7pt（缩放到论文栏宽后仍可读）；线条粗细统一。
- [ ] 图中术语与正文/公式符号一致，缩写首次出现有解释。
- [ ] `spec.yaml` 与导出文件同名同目录，可一键重渲染。
- [ ] 图注（caption）自洽：说明模块、箭头、loss 与关键设计选择。

## 失败处理

| 情况 | 处理 |
|---|---|
| 节点太多塞不下 | 拆成总览图 + 细节图（附录）；或把同构模块用「×N」note 表示 |
| 交叉线太多 | 调整 row/col 重排，把交互模块放相邻列；必要时拆子图 |
| 导出 PDF/PNG 失败 | 先交付 SVG；安装 cairosvg 或系统 `rsvg-convert` 后可转 |
| 字体/符号缺失 | 用内置无衬线字体，数学符号用文本而非特殊字形 |
| 与正文不一致 | 先冻结正文术语表，再改 spec；把术语表写进图注 |
| 审稿人说图难读 | 减少节点、加分组标题、把细节移到附录图；加大字号 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 渲染架构图 | `figure_architecture`、`sciforge fig arch --spec figures/fig1_arch.yaml -o figures/fig1_arch.svg` |
| 结果/曲线图 | `figure_plot`（见 `paper-figures-results` skill） |
| 表格 | `figure_table` |
| 写作衔接 | `paper_section`（图注与正文引用） |

## 示例

```yaml
title: "Method Overview"
width: 1200
groups:
  - {id: enc, label: "Encoder", row: 1, col: 0, span: 2}
nodes:
  - {id: x, label: "Input x", kind: data, row: 0, col: 0}
  - {id: e, label: "ViT Encoder", sub: "L layers", kind: module, group: enc, row: 1, col: 0}
  - {id: f, label: "Cross-Attn Fusion", kind: op, row: 1, col: 2}
  - {id: y, label: "Output", kind: tensor, row: 2, col: 2}
  - {id: l, label: "L_task + λL_reg", kind: loss, row: 3, col: 2}
edges:
  - {from: x, to: e}
  - {from: e, to: f, label: "tokens"}
  - {from: f, to: y}
  - {from: y, to: l}
```

## 进一步阅读（渐进披露）

完整 DSL 字段、分层布局技巧与顶会图注模板见 `references/arch-spec-template.md`。
