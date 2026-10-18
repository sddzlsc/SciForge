# 架构图 DSL 与布局模板

## 1. 节点类型（kind）

| kind | 用途 | 视觉建议 |
|---|---|---|
| `data` | 输入数据/数据集 | 胶囊形，浅灰 |
| `tensor` | 张量/特征/表示 | 细长条，标注 shape |
| `module` | 网络模块/block | 圆角矩形，主色 |
| `op` | 算子/融合/注意力 | 菱形或小圆角矩形 |
| `loss` | 损失/目标 | 六边形或红色描边 |
| `note` | 说明/超参/重复次数 | 虚线框，浅黄 |

## 2. 布局规则

1. **分层**：按数据流分 3-5 行：输入 → 表示/编码 → 核心模块 → 输出 → 损失。
2. **分组**：同源模块放进同一个 `group`（虚线面板），组标题放左上角，span 控制跨列。
3. **对齐**：同层节点 `row` 相同、`col` 递增；旁路连接放上方或下方行，避免穿过节点。
4. **宽度**：`width` 建议 1000-1400；节点 label ≤14 字符，超长用 `sub` 放细节。
5. **边**：默认实线；表示可学习/监督用虚线加 label；箭头统一方向。

## 3. 顶会 Figure 1 常用结构

### A. 编码器-解码器（多模态）

```
输入 A ──▶ Encoder A ─┐
                     ├─▶ Fusion ─▶ Decoder ─▶ 输出
输入 B ──▶ Encoder B ─┘                └─▶ Loss
```

### B. 训练管线（两阶段）

```
Stage 1: 预训练（自监督）──▶ 冻结/解冻 ──▶ Stage 2: 任务微调 ──▶ 评测
```

### C. 模块细节（推荐/路由）

```
Tokens ─▶ Router ─┬─▶ Expert 1 ─┐
                  ├─▶ Expert 2 ─┼─▶ Weighted Sum ─▶ 输出
                  └─▶ Expert N ─┘
```

## 4. 图注模板

> **Figure 1: Method overview.** Given input \(x\), our method first encodes it with ... (a).
> The core module (b) fuses ... via ... . Training uses the task loss \(\mathcal{L}_{task}\)
> and a regularization term \(\mathcal{L}_{reg}\) (c). Dashed arrows denote ... .

要点：先说整体，再按 (a)(b)(c) 对应图中分组；说明箭头/虚线/颜色的含义；不重复正文大段文字。

## 5. 渲染与检查

```bash
sciforge fig arch --spec figures/fig1_arch.yaml -o figures/fig1_arch.svg
# 需要位图时
sciforge fig arch --spec figures/fig1_arch.yaml -o figures/fig1_arch.pdf
```

渲染后逐项检查：文字是否被裁切、箭头是否穿过文字、分组框是否包住节点、缩放 50% 后文字是否可读。
