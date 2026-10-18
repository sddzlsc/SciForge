---
name: daily-arxiv-digest
description: 当需要每天跟踪 arXiv 新论文、按类别/关键词筛选并生成中文速览时使用；产出每日论文摘要清单与值得精读的候选列表。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [literature, meta]
---

# 每日 arXiv 速览（Daily arXiv Digest）

## 何时使用（触发条件）

- 每天早上想用 10 分钟了解本方向新论文，而不是刷 arXiv 列表。
- 需要按类别（cs.CV/cs.LG/cs.CL）、关键词或作者过滤新论文。
- 要维护「值得精读」候选队列，并与已有文献库去重。

## 输入 / 输出

- 输入：arXiv 类别（可多个）、关键词/作者过滤、日期（默认最新）、数量上限。
- 输出：`daily/<date>.md`（标题 + 一句话中文总结 + arXiv 链接 + 推荐分级）、更新后的候选精读队列。

## 工作流

1. **拉数据**：`lit_daily --category cs.CV --limit 30`，数据来自 daily-arXiv-ai-enhanced 的每日 JSONL（含 AI 增强中文摘要）；也可用 `lit_search` 取当日 arXiv 新提交。
2. **过滤**：按关键词（标题+摘要）、作者、机构过滤；去掉与已有 PaperLibrary 重复的条目。
3. **三档分级**：A=必读（直接相关+强结果）、B=关注（方法可迁移）、C=存档（同主题但增量）；给出分级理由一句话。
4. **补上下文**：对 A 档，`paper_read` 取 abstract/method 关键段，标出「与已有工作的关系」（改进/对比/新设定）。
5. **写简报**：按主题分组的 Markdown；每篇 2-3 行：问题、方法关键词、结果数字、为什么值得读。
6. **入库**：把 A/B 档加入 PaperLibrary 并打 tag（日期/方向）；更新 `notes/daily-index.md` 累计索引。
7. **周复盘**：每周把 A 档合并成 mini-survey，交给 `literature-survey` 或 `innovation-mining`。

## 质量检查清单

- [ ] 每篇有一句话中文总结（不是机翻标题）。
- [ ] 链接为 arXiv abs 页，ID 正确可打开。
- [ ] 与已有文献库去重，重复条目注明「已入库」。
- [ ] 分级有理由，A 档 ≤5 篇（否则失去筛选意义）。
- [ ] 中文摘要来自可靠来源（daily arXiv 增强摘要或自己读后写）。
- [ ] 简报按主题分组，而不是按 arXiv 提交顺序。
- [ ] 注明数据日期与类别，可追溯。

## 失败处理

| 情况 | 处理 |
|---|---|
| daily 源当天未更新 | 用 `lit_search --since 1d` 直接查 arXiv；在简报注明数据来源 |
| 网络 429/超时 | 稍后重试或用本地缓存；不要阻塞晨间流程 |
| 增强摘要是英文/机翻 | 自己用标题+abstract 重写一句话；标注来源 |
| 类别太杂（cs.LG 巨多） | 叠加关键词过滤 + 限定 A 档数量；订阅更窄的交叉类别 |
| 看不过来 | 只读 A 档；B/C 档攒到周复盘；用 `paper_ask` 快速问答 |
| 重复推送 | 维护已推送 ID 集合（`daily/seen.json`），每日先过滤 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 每日增强摘要 | `lit_daily`、`sciforge lit daily --category cs.CV --limit 30` |
| 关键词/日期检索 | `lit_search --since 1d` |
| 入库与笔记 | `paper_fetch`、`paper_note`、`paper_read` |
| 去重 | PaperLibrary `search` / `get` |
| 脚本兜底 | `daily-arxiv-digest/scripts/fetch_daily.py` |

## 示例

```bash
sciforge lit daily --category cs.CV --limit 30
sciforge lit search "video diffusion" --source arxiv --since 1d --limit 20
python sciforge/skills_lib/daily-arxiv-digest/scripts/fetch_daily.py --category cs.CV --limit 20
```

## 进一步阅读（渐进披露）

如需直接拉取原始 JSONL（网络可用时），用同目录 `scripts/fetch_daily.py`；数据格式与字段见脚本头部注释。
