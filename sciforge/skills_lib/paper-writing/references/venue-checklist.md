# Venue 检查清单与段落模板

## 1. 主要 venue 速查（以官方最新 CFP 为准）

| Venue | 页数（正文） | 模板 | 匿名 | 补充材料 | 备注 |
|---|---|---|---|---|---|
| NeurIPS | 8 页 + 无限附录 | neurips.sty | 双盲 | 允许 | 贡献/局限/影响声明 |
| ICML | 8 页（含 references 不计） | icml.sty | 双盲 | 允许 | 强调理论/方法严谨 |
| ICLR | 9 页（references 不计） | iclr.sty | 双盲 | 允许 | OpenReview 公开评审 |
| CVPR/ICCV | 8 页（references 不计） | cvpr.sty | 双盲 | 允许 | 强调实验与可视化 |
| ECCV | 14 页（含 references） | eccv.sty | 双盲 | 允许 | 欧洲时区规则 |
| ACL/EMNLP | 8 页（长短文不同） | acl.sty | 双盲 | 允许 | 伦理声明、局限性章节 |
| AAAI | 7 页 + 参考文献 | aaai.sty | 双盲 | 允许 | 严格格式检查 |

> 页数、模板、附录政策每年可能调整，投稿前以官网 CFP 为准。`sciforge write init --venue X` 只会生成对应骨架，不保证当年格式。

## 2. 各节段落模板

### Abstract（120-200 词）

1. 问题与重要性（1-2 句）；
2. 现有方法局限（1 句）；
3. 本文方法一句话（1-2 句）；
4. 关键结果数字（1-2 句）；
5. 意义/可用性（1 句，可选）。

### Intro 贡献列表

```latex
Our contributions are:
\begin{itemize}
  \item We identify \emph{<gap>} and show that \emph{<insight>}.
  \item We propose \emph{<method>}, which \emph{<mechanism>}.
  \item On \emph{<datasets>}, \emph{<method>} improves \emph{<metric>} by \emph{<x>} over \emph{<baseline>}.
\end{itemize}
```

### Method

1. Problem setup：符号表、输入输出、目标函数；
2. 整体框架 + Figure 1 引用（先讲数据流，再讲模块）;
3. 关键模块：动机 → 设计 → 直觉/公式；
4. 训练目标与优化细节（放正文或附录）。

### Experiments

1. 实验设置：数据、指标、baseline、实现细节（可放附录）；
2. 主结果表（对应 claim 1）；
3. 消融（对应 claim 2）；
4. 效率/鲁棒性/定性分析；
5. 失败案例与局限（提升可信度）。

### Related Work

按 3-5 条技术路线分组，每段结尾必须有：「In contrast, our work ...」

## 3. 投稿前 20 分钟检查

- [ ] 页数符合 CFP；模板版本正确；匿名化（作者/致谢/代码链接）。
- [ ] 所有图表被正文引用；编号连续；图注完整。
- [ ] 无 `??`、无 `[CITATION NEEDED]`、无 TODO/占位符。
- [ ] 参考文献格式统一；无重复条目；每条引用真实存在。
- [ ] 数字在 abstract/intro/实验/结论四处一致。
- [ ] 补充材料可独立打开；代码/数据匿名链接有效。
- [ ] 附录含完整超参、数据划分、额外结果。
- [ ] 局限性/伦理/影响声明按 venue 要求提供。
