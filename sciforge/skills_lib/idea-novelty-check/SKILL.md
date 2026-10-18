---
name: idea-novelty-check
description: 当提出研究 idea 后需要做新颖性终检、撞车排查或准备 novelty 辩护时使用；产出检索证据、相似工作对比表与差异化定位。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [ideation, review]
---

# 创新性终检（Idea Novelty Check）

## 何时使用（触发条件）

- 新 idea 定稿前，需要确认近 6-12 个月没有高度相似工作。
- 审稿人质疑 novelty，需要准备对比证据。
- 组会/开题答辩前，需要预演「这跟 XX 有什么区别」的追问。

## 输入 / 输出

- 输入：idea 一句话主张、关键机制关键词、相关方向、时间范围。
- 输出：`novelty-check.md`：相似工作列表（含差异维度）、撞车风险评级、差异化定位陈述、需要补的实验/引用。

## 工作流

1. **提炼检索要素**：从 idea 抽 3-5 个不可替代的关键词（机制名、任务、约束、指标），列出同义词与反向表述。
2. **多轮检索**：`lit_search` 组合检索（近 6/12/24 个月）；覆盖 arXiv、OpenAlex、Crossref、HF Papers；会议最新 accepted papers 用 web 检索或官方 proceedings 人工核对。
3. **逐篇对比**：对 Top 5 相似工作建对比表——问题、机制、数据、指标、约束；标出「相同 / 相似 / 不同」。
4. **判断撞车等级**：P0 同机制同设定（撞车）、P1 同机制不同设定（需差异化）、P2 不同机制同目标（可共存）、P3 无关。
5. **写差异化定位**：一句话「相比 X，我们在 Y 上不同，因此能 Z」；准备 2-3 个可验证差异点。
6. **补证据**：若 P0/P1，补引用与对比实验；若 P2，强调设定差异与互补性。
7. **落到论文**：把对比表写进 Related Work / rebuttal；持续跟踪（设置 monthly recheck）。

## 质量检查清单

- [ ] 用了至少 3 组不同关键词、2 个以上数据源检索。
- [ ] 时间覆盖近 6-12 个月，含最新会议 accepted 列表。
- [ ] Top 相似工作有逐维度对比，不是只列标题。
- [ ] 撞车等级有理由与证据链接。
- [ ] 差异化定位可被实验验证，不是文字游戏。
- [ ] 已检查作者/实验室是否在做同方向（作者主页/最新预印本）。
- [ ] 结论中明确「仍需补的实验/引用」。

## 失败处理

| 情况 | 处理 |
|---|---|
| 检索不到（新领域） | 扩大到相邻领域 + 用不同术语；查 workshop/预印本服务器（arXiv/SSRN） |
| 发现高度相似（P0） | 与对方对比找差异（设定/约束/规模）；做正面对比实验；必要时换方向 |
| 并发工作同时出现 | 引用并对比；强调独立发现与不同分析角度 |
| 无法判断相似度 | 请同组人 blind review 两段摘要；用结构化对比表投票 |
| 检索被限流 | 降速、换数据源、缓存结果；过几小时补查 |
| 差距说不清 | 回到 claim 模板，用「在什么条件下、比谁、好多少」重写 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 组合检索 | `lit_search`、`sciforge lit search` |
| 精读相似论文 | `paper_read`、`paper_ask` |
| 多篇对比 | `paper_compare` |
| 生成对比表 | `figure_table`、Markdown 表格 |
| 上游 | `innovation-mining`、`research-ideation` skill |

## 示例

```bash
sciforge lit search "memory compression long video diffusion" --since 2025 --source arxiv --limit 25
sciforge lit search "hierarchical memory video generation" --source arxiv --limit 25
sciforge lit search "hierarchical memory video generation" --source openalex --limit 25
# 对 Top-5 相似工作逐篇 paper_compare，填对比表
```

## 进一步阅读（渐进披露）

对比表模板、撞车风险分级与差异化话术见 `references/novelty-checklist.md`。
