# 文献检索：免费数据源清单与配置

> 目标：在不购买数据库、不绕过付费墙的前提下，尽可能覆盖题录、摘要与合法全文。
> 本页同时说明你列出的数据库里**哪些可以直接接、哪些只能查到题录、哪些没有公开接口**。

## 一、已接入的免费 API（零 Key 即可用）

| 渠道 | 覆盖 | 能拿到 | Key |
|---|---|---|---|
| **arXiv** | 物理/CS/数学/AI 预印本 | 题录 + 摘要 + 全文 PDF | 否 |
| **OpenAlex** | 2.5 亿+ 学术作品，跨库 | 题录、作者、DOI、开放获取链接 | 否（填邮箱更稳） |
| **Crossref** | 全 DOI 元数据 | 权威题录、BibTeX、部分摘要 | 否 |
| **PubMed** | 医学 / 生命科学 | 题录 + 摘要（NCBI E-utilities） | 否（建议配 email） |
| **Europe PMC** | PubMed + PMC + 预印本 | 题录 + 摘要 + OA 全文链接 | 否 |
| **OpenReview** | ICLR / NeurIPS / ICML 等 | 题录 + 摘要 + 公开评审 + PDF | 否 |
| **HF Papers** | HuggingFace 每日/趋势 | 题录 + 摘要 | 否 |
| **daily-arXiv-ai-enhanced** | 每日 arXiv | 题录 + DeepSeek 中文增强摘要 | 否 |

用法：

```bash
sciforge lit search "video diffusion transformer" --source arxiv --limit 20
sciforge lit search "video diffusion" --source pubmed --limit 20          # 医学/生命科学
sciforge lit search "protein folding" --source europe_pmc --limit 20
sciforge lit search "diffusion transformer" --source openreview --limit 20 # ICLR/NeurIPS
sciforge lit search "..." --source openalex --source crossref --limit 10   # 多源合并
```

## 二、你的清单：哪些能接、怎么接

### ✅ 有公开免费 API（已接入）

- **OpenAlex、Crossref**：题录/摘要/引用关系（OpenAlex 有 cited_by 数据）
- **PubMed、Europe PMC**：生命科学题录、摘要、OA 全文
- **OpenReview**：AI 会议论文与公开评审
- **Semantic Scholar**：有免费 API（限流，建议免费 Key）。**按你之前的要求当前默认未启用**；
  适配器代码保留，需要时可恢复为可选渠道。
- **arXiv、bioRxiv/medRxiv、SSRN、Research Square**：预印本源。已通过 arXiv + Europe PMC
  覆盖；bioRxiv/medRxiv 被 Europe PMC 索引，SSRN/Research Square 部分可通过 Crossref/OpenAlex 检索。

### 🟡 没有公开 API，但可以免费拿到「题录」

- **IEEE Xplore、ACM DL、SpringerLink、ScienceDirect、Wiley、Taylor & Francis、JSTOR**
  → 通过 **Crossref 出版社过滤（member ID）** 免费检索题录/DOI/摘要；**全文**需订阅或找 OA 版本。
  已接入 `--source crossref --publisher <name>`：

```bash
sciforge lit search "diffusion model acceleration" --source crossref --publisher ieee --limit 20
sciforge lit search "retrieval augmented generation" --source crossref --publisher acm --limit 20
# 可选：ieee / acm / springer / elsevier / wiley / taylor_francis / jstor
```

- **CNKI / 万方 / 维普 / 国家哲学社会科学文献中心 / 百度学术**
  → 无公开 API（订阅/反爬/需登录）。能通过 Crossref/OpenAlex 命中部分有 DOI 的中文期刊题录，
  但系统化检索仍需机构账号；本工具**不做登录态爬取**。
- **ProQuest / EThOS / CNKI 学位论文 / 各学协会会议集**
  → 学位论文与会议全文多为订阅或访问受限；会议集（CVF/ACL Anthology/PMLR）公开但无统一 API，
  可用 OpenReview + arXiv + Web 检索组合覆盖。
- **Web of Science / Scopus**
  → 商业数据库，无免费 API；引用关系可用 **OpenAlex** 部分替代（cited_by、related works）。
- **Google Scholar**
  → 无官方公开 API；抓取违反其条款且不稳定，因此不接入。
- **RePEc**
  → 无标准公共 API；经济学内容可用 OpenAlex/Crossref 覆盖一部分。

### ⚪ 灰色文献与统计数据（免费 API 存在，当前未接入）

政府网站、国家统计局、世界银行、WHO、OECD、联合国等大多有开放 API/SDMX 接口
（World Bank `api.worldbank.org`、WHO GHO、OECD SDMX、UN SDG API）。这些**不是学术论文库**，
当前 SciForge 聚焦论文工作流，暂未接入；需要的话可以加一个「数据查询」工具族。

## 三、全文获取（只走合法来源）

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | arXiv / OpenReview PDF | 直接下载 |
| 2 | Unpaywall | 按 DOI 找合法 OA 版本（需邮箱） |
| 3 | OpenAlex OA / Europe PMC OA | 开放获取链接 |
| 4 | instsci（可选） | 复用你的学校/图书馆订阅（WebVPN/CARSI/EZProxy） |
| 5 | 手动合法 PDF | `sciforge read <本地路径>` 精读 |

> 不集成 Sci-Hub 等未授权镜像（版权与法律风险）。付费墙论文：查预印本 → Unpaywall →
> 机构订阅 → 手动下载合法 PDF。

## 四、配置（只需要邮箱，可选）

GUI：**文献** 页 → 「检索设置」→ 联系邮箱 / Unpaywall 邮箱 / 每日 arXiv 语言。

```toml
[literature]
email = "you@example.com"
# unpaywall_email = "you@example.com"
daily_arxiv_language = "Chinese"
```

`email` 会作为 Crossref/OpenAlex 礼貌池与 Unpaywall 查询参数；不配置也能用，只是配额与 OA 命中略低。

## 五、历史会话

GUI 对话页点 **🕘 历史** 可查看 `.sciforge/sessions/` 下的所有历史对话（含 CLI/网页会话），
点击即可恢复查看；继续发消息会接上该会话的上下文。

```bash
ls .sciforge/sessions/            # 每个会话一个目录
sciforge chat                     # CLI 交互
```
