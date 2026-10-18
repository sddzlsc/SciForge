# 投稿与 Camera-ready 检查清单

## 1. 投稿前（匿名版）

- [ ] 官网 CFP 的页数、模板、附录政策已核对（当年版本）。
- [ ] 官方 style 文件为最新；不修改 style 内部边距/字号。
- [ ] 作者、单位、致谢、基金、个人主页全部移除。
- [ ] 代码链接为匿名仓库（anonymous.4open.science 等），无 commit 作者信息。
- [ ] PDF 元数据检查：`pdfinfo paper.pdf`（Title/Author/Producer）。
- [ ] 补全 Reproducibility Checklist（NeurIPS/ICLR 等必填）。
- [ ] 伦理声明/局限性章节按 venue 要求提供。
- [ ] 图表在页数限制内，附录单独编页。
- [ ] 补充材料 PDF 独立可打开，正文说明其内容。
- [ ] 提交系统里填写的 abstract 与 PDF 一致。

## 2. 编译清理

```bash
latexmk -C                      # 清理中间文件
xelatex main.tex && bibtex main && xelatex main.tex && xelatex main.tex
pdfinfo main.pdf | head
```

常见错误速查：

| 报错 | 原因 | 处理 |
|---|---|---|
| `Undefined control sequence` | 宏包缺失或拼写 | 查日志首个 error，补 `\usepackage` |
| `Citation undefined` | bib 未编译/键错误 | 重跑 bibtex，核对 key |
| `Missing $ inserted` | 数学符号未包裹 | 定位行号补 `$...$` |
| `Overfull \hbox` | 内容超宽 | 调列宽/换行/缩图；不严重可忽略但尽量消除 |
| `File ... not found` | 图路径错误 | 用相对 `figures/xxx.pdf`，确认文件存在 |

## 3. Camera-ready

- [ ] 补全作者、单位、邮箱、致谢、基金号。
- [ ] 按版权表要求填写（ACM/IEEE 版权块）。
- [ ] 处理审稿人要求的修改，保持页数限制（通常可加 1 页）。
- [ ] 代码/数据正式开源链接替换匿名链接。
- [ ] 最终 PDF 通过 Portability 检查（字体嵌入：`pdffonts main.pdf`）。
- [ ] 补充材料与正文版本一致（图表编号不变）。
- [ ] 上传前在干净目录重新编译一次，避免本地缓存差异。
