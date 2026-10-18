---
name: paper-retrieval-oa
description: 当论文没有开放获取 PDF、需要找预印本/机构版本/作者主页版本时使用；产出可下载的合法全文来源与访问受阻时的降级方案。
version: 0.1.0
license: MIT
compatibility: "sciforge>=0.1"
metadata:
  author: SciForge
  tags: [literature, reading]
---

# 开放获取全文获取（Open-Access Paper Retrieval）

## 何时使用（触发条件）

- 知道论文标题/DOI/arXiv ID，但 `paper_fetch` 找不到可下载 PDF。
- 需要判断一篇论文是否 OA、该走哪个合法来源（arXiv/OpenReview/PMC/机构库）。
- 要做批量 DOI 全文下载，希望自动化并保留来源与许可证信息。

## 输入 / 输出

- 输入：DOI / arXiv ID / 标题 / 出版商页面 URL；机构访问条件（若有）。
- 输出：本地 PDF 路径 + 来源（arXiv/Unpaywall/OpenAlex/PMC/作者主页）+ 许可证 + 获取失败时的替代方案（HTML/摘要/馆际互借）。

## 工作流

1. **先查 OA**：`paper_fetch` 会依次尝试 arXiv、Unpaywall、OpenAlex 的 OA 链接；拿到 `is_oa` 与 `best_oa_location`。
2. **按优先级找全文**：arXiv（最稳）→ OpenReview/ACL Anthology/PMLR/CVF OpenAccess（会议论文）→ PMC/Europe PMC（生物医学）→ 机构仓库（MIT DSpace 等）→ 作者主页（Google Scholar 的 [PDF] 链接）。
3. **验证完整性**：打开 PDF 检查是否是最终版/预印本、页数是否完整、有无 access denied 页面。
4. **记录来源**：把 source、license、版本（preprint/accepted/published）写进 PaperRecord 与笔记。
5. **批量处理**：用 `scripts/oa_lookup.py` 输入 DOI 列表，输出 OA 状态与候选链接 CSV/JSON。
6. **合法兜底**：仍拿不到时——读摘要+OpenReview 讨论+作者博客/代码 repo 的 README；通过机构 VPN/图书馆合法访问；不要使用盗版镜像。
7. **更新库**：成功后 `paper_fetch` 入库；失败记录到 `notes/missing_fulltext.md` 供人工跟进。

## 质量检查清单

- [ ] PDF 可正常打开且是目标论文（标题/作者/DOI 匹配）。
- [ ] 记录版本类型（camera-ready vs preprint）与许可证。
- [ ] 来源链接可复现（保存 URL 与获取日期）。
- [ ] 不是出版商登录页/摘要页伪装成 PDF。
- [ ] 批量结果去重，失败的 DOI 有明确原因（无 OA/需订阅/链接失效）。
- [ ] 所有获取途径合法（OA、机构订阅、作者自存档）。

## 失败处理

| 情况 | 处理 |
|---|---|
| Unpaywall 无结果 | 查 arXiv 标题检索、作者主页、OpenReview；期刊官网看是否 free access |
| 429/限流 | 降速、加 email（Unpaywall 礼貌池）、换 OpenAlex |
| DOI 无效/重定向 | 用 Crossref 查元数据；用标题在 S2/Google Scholar 找正确 DOI |
| 只有 HTML 全文 | 用 `pymupdf`/`trafilatura` 抽正文；仍按论文记入 notes |
| 需要机构订阅 | 用学校 VPN/EZproxy/图书馆代理合法访问；或走馆际互借 |
| 预印本与正式版不一致 | 以正式版为准；关键差异写进笔记 |
| 批量下载被封锁 | 加延迟、分批次；遵守 robots/许可，停止非授权抓取 |

## SciForge 工具与 CLI 对应

| 步骤 | 工具 / CLI |
|---|---|
| 自动找 OA | `paper_fetch`（arXiv/Unpaywall/OpenAlex 链） |
| 检索候选 | `lit_search`、`paper_lookup` |
| 批量 OA 查询 | `paper-retrieval-oa/scripts/oa_lookup.py` |
| 机构访问（可选） | `instsci`（若已安装，见外部生态目录） |
| 入库/笔记 | `paper_read`、`paper_note` |

## 示例

```bash
# 1) 单篇：给 DOI 直接找 OA 并入库
sciforge read 10.1109/CVPR.2024.00123
# 2) 批量查 OA 状态
python sciforge/skills_lib/paper-retrieval-oa/scripts/oa_lookup.py dois.txt --json -o oa_status.json
```

## 进一步阅读（渐进披露）

批量查询脚本 `scripts/oa_lookup.py` 使用 Unpaywall/OpenAlex 公共 API（需文明使用），参数见脚本 `--help`。
