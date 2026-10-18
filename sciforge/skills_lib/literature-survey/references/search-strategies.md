# 检索式与筛选细则

## 1. 检索式构造

每个主题准备四类词，用布尔组合（不同源语法不同，工具内部会拼装）：

- 任务词：video generation / long-context / 3D reconstruction / reasoning ...
- 方法词：diffusion transformer / RLHF / mixture-of-experts / retrieval-augmented ...
- 数据/场景词：benchmark 名、数据集名、领域名（medical、autonomous driving）
- 同义词与缩写：LLM / large language model；VLM / vision-language model；NeRF / neural radiance field

示例：`(video diffusion OR video DiT) AND (long video OR long-context) AND (temporal consistency OR memory)`。

## 2. 去重归一化

1. 优先用 arXiv ID；有版本号时去掉 `vN`（`2401.00001v2` → `2401.00001`）。
2. DOI 统一小写、去掉 `https://doi.org/` 前缀。
3. 无 ID 时标题归一化：小写、去标点、合并空白，取前 80 字符作为 key。
4. 同一论文多个版本保留最新提交；会议版与 arXiv 版标记 `venue` 后保留一条主记录。

## 3. 筛选优先级

| 优先级 | 条件 |
|---|---|
| P0 | 主题直接相关且发表于目标 venue / 近 12 个月高引 |
| P1 | 提供关键 baseline、数据集、评测协议 |
| P2 | 方法可迁移、提供失败分析或 negative results |
| 丢弃 | 纯应用报告无可复用方法、预印本已被正式工作取代 |

首轮 20-40 篇足够；写 related work 时再按「技术路线 + 每线 3-5 篇」补。

## 4. 引文滚雪球

- 向后：读种子论文的 Related Work 与 References，找被反复引用的奠基工作。
- 向前：用 Semantic Scholar 的 citations 接口找引用种子论文的后续工作（`lit_search` 支持标题检索后取引用）。
- 收敛信号：新增论文不再带来新概念/新 baseline 时停止。

## 5. survey.md 推荐骨架

```markdown
# <主题> 文献调研（截至 <日期>）
## 1. 问题定义与评测设定
## 2. 技术路线 A：...（起点 → 改进 → SOTA → 未解问题）
## 3. 技术路线 B/C/D ...
## 4. 数据集与指标对比表
## 5. 研究空白（gap）与可验证问题
## 附录 A：检索式与命中统计
## 附录 B：参考文献（BibTeX key 列表）
```
