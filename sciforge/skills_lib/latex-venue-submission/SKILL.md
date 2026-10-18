---
name: latex-venue-submission
description: 当准备 LaTeX 投稿工程、套用会议模板、处理页数/参考文献/Camera-ready 时使用；产出可编译的投稿包与逐项合规检查。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [venue, writing]
---

# LaTeX 投稿工程（LaTeX & Venue Submission）

## 何时使用（触发条件）

- 要新建/迁移论文 LaTeX 工程，套用 NeurIPS/ICML/ICLR/CVPR/ACL 等官方模板。
- 需要处理页数限制、匿名化、参考文献风格、附录、补充材料。
- Camera-ready 阶段要生成作者版、补致谢/基金、清理批注。

## 输入 / 输出

- 输入：venue 与年份、论文内容、图表、references.bib、作者信息（Camera-ready 时才填）。
- 输出：可编译的 `paper/` 工程（main.tex、sections/、refs.bib、官方 style 文件）、编译日志、`submission-checklist.md`。

## 工作流

1. **取官方模板**：从 venue 官网下载当年样式包（不要用旧版）；`paper_init`/`sciforge write init --venue X` 生成骨架后再替换 style 文件。
2. **定结构与页数**：正文页数按 CFP；附录单独计数；先搭 section 骨架再填内容。
3. **匿名化**：删除作者/单位/致谢/基金；代码链接用匿名仓库；检查 PDF 元数据与 `\pdfinfo` 不泄露身份。
4. **参考文献**：统一 BibTeX 风格（natbib/ACL）；`paper_cite` 生成后去重、检查年份/venue；无 arXiv 的条目补 DOI。
5. **图表与排版**：图用 PDF/SVG；宽表用 `table*`；避免 overfull hbox；公式编号连贯。
6. **编译**：`xelatex`（中文）/`pdflatex` + `bibtex` 多轮，直到无 undefined reference；`paper_compile` 自动检测引擎。
7. **合规自检**：页数、字体、边距、附录、伦理声明、reproducibility checklist（部分 venue 必填）。
8. **打包**：清除 aux/log/bbl 中间文件；补充材料单独 PDF；确认上传文件名与大小限制。

## 质量检查清单

- [ ] 模板版本与当年 CFP 一致，页数不超。
- [ ] 无 `??`、undefined citation、duplicate label；编译零 error。
- [ ] 匿名化彻底（作者、致谢、代码链接、PDF 元数据）。
- [ ] 图/表编号与正文引用一致；图注在同类 venue 风格内。
- [ ] 参考文献无重复、年份正确、格式统一。
- [ ] 附录包含全部超参与额外结果，可独立阅读。
- [ ] 补充材料可打开，文件路径在正文中给出（若允许）。
- [ ] Camera-ready 时补全作者、致谢、基金、版权表。

## 失败处理

| 情况 | 处理 |
|---|---|
| 编译报错 | 看日志首个 error；常见：缺包、未定义引用、特殊字符未转义（& % _ #）、字体缺失 |
| 页数超限 | 压缩相关工作和重复表述；细节移附录；图表改双栏/合并；减少空行 |
| 中文编译失败 | 用 xelatex + ctex 宏包；确保字体存在；避免 pdflatex 编中文 |
| 参考文献格式乱 | 统一 `\bibliographystyle`；用 venue 官方 bst；清理 bib 重复 key |
| 匿名泄露 | 检查 `\author`、ack、github 链接、PDF metadata（`pdfinfo`） |
| 平台上传失败 | 检查文件大小/文件名/是否含 aux；按会议要求重新打包 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 初始化/编译/自检 | `paper_init`、`paper_compile`、`paper_review`、`sciforge write compile` |
| 章节内容 | `paper_section` |
| 引用 | `paper_cite` |
| 图表 | `figure_architecture`、`figure_plot`、`figure_table` |
| 工程模板 | `sciforge/templates/paper/`（cvpr/neurips/iclr/acl/generic） |

## 示例

```bash
sciforge write init --venue neurips --title "My Paper" --dir paper
sciforge write compile --paper paper/ --engine xelatex
sciforge write check --paper paper/     # 引用完整性/占位符/图表编号
```

## 进一步阅读（渐进披露）

投稿打包、匿名化与 Camera-ready 清单见 `references/venue-submission-checklist.md`。
