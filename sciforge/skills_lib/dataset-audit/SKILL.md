---
name: dataset-audit
description: 当使用公开或自建数据集前需要体检数据质量、标签噪声、分布偏移与许可合规时使用；产出数据审计报告与清洗方案。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [experiment, reproducibility]
---

# 数据集审计（Dataset Audit）

## 何时使用（触发条件）

- 使用新数据集（公开/自采/爬取）训练前，需要确认数据质量与许可。
- 模型效果异常，怀疑标签噪声、类别不平衡、分布偏移或数据泄漏。
- 准备发布数据集/benchmark，需要写 datasheet 与清洗记录。

## 输入 / 输出

- 输入：数据路径/下载脚本、标注格式、任务定义、许可证文本。
- 输出：`dataset-audit.md`（规模/分布/缺失/重复/标签质量/许可）、清洗脚本与清洗后 manifest、风险清单。

## 工作流

1. **完整性**：样本数、标注数、split 大小、缺失文件/损坏文件比例；与官方声称数字对比。
2. **分布**：类别/长度/分辨率/语言/来源分布；长尾与不平衡程度；画直方图（`figure_plot`）。
3. **重复与泄漏**：精确 hash + 近重复检测；train/test 跨 split 重复；与预训练/评测集重叠检查。
4. **标签质量**：抽样 100-500 条人工核验；统计多标注者一致性（Cohen's kappa）；找系统性错标模式。
5. **脏数据**：空文本、全黑图、截断、错位、编码错误、异常值；统计各失败类型比例。
6. **许可与合规**：许可证是否允许研究与再分发；含个人信息/敏感内容需脱敏；引用与 datasheet 要求。
7. **清洗与记录**：写脚本（可复现）→ 生成清洗后 manifest（文件数/hash）→ 记录删除规则与影响。
8. **报告**：`dataset-audit.md` + 可视化 + 已知局限；把风险写进论文 limitation。

## 质量检查清单

- [ ] 规模/类别/split 与官方文档一致，差异有解释。
- [ ] 缺失与损坏文件比例、处理方式明确。
- [ ] 训练/测试无重复；与常见预训练集的重叠已评估。
- [ ] 标签抽样核验记录（样本量、错误率、错误类型）。
- [ ] 类别不平衡有量化（比例/长尾曲线）。
- [ ] 清洗脚本可复现，清洗前后 hash 可核对。
- [ ] 许可证与合规风险明确，敏感数据已脱敏。
- [ ] 数据卡片（datasheet）包含来源、采集方式、偏差与预期用途。

## 失败处理

| 情况 | 处理 |
|---|---|
| 数据量与官方不符 | 核对版本/子集/下载完整性；以实测为准并在报告中标注 |
| 标签噪声高 | 清洗/重标/软标签；噪声率过高则换数据集 |
| 类别严重不平衡 | 重采样/加权/分层划分；报告 per-class 指标而非只报均值 |
| 发现泄漏 | 删除重复样本、按来源重新划分；重跑所有结果 |
| 许可不明/不可再分发 | 只提供下载脚本与哈希；标注「仅限研究」；必要时换数据 |
| 敏感数据 | 脱敏/聚合/申请审批；不把原始数据提交到 git |
| 清洗后效果下降 | 记录清洗规则对指标的影响；在论文中报告清洗前后对比 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 数据统计/可视化 | `figure_plot`、本地 Python/pandas |
| 泄漏/重复检查 | `benchmark-eval-protocol/references/leakage-checklist.md` |
| 数据准备脚本 | `exp_scaffold` 生成的 `src/data.py`、`scripts/prepare_data.sh` |
| 远程数据 | `remote_sync`（大文件） |
| 复现记录 | `reproducibility-package` skill |

## 示例

```python
# 最小审计片段：规模/缺失/类别/重复
import json
from collections import Counter
from pathlib import Path

rows = [json.loads(line) for line in Path("data/train.jsonl").read_text().splitlines()]
print("samples:", len(rows))
print("labels:", Counter(row.get("label") for row in rows).most_common(10))
print("empty text:", sum(1 for row in rows if not str(row.get("text", "")).strip()))
hashes = Counter(hash(json.dumps(row, sort_keys=True)) for row in rows)
print("duplicates:", sum(count - 1 for count in hashes.values() if count > 1))
```

## 进一步阅读（渐进披露）

审计指标定义、datasheet 模板与近重复检测方法见 `references/audit-checklist.md`。
