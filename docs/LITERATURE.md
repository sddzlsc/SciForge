# 文献获取：渠道、能力与配置

> 状态：2026-10-18 实测。所有渠道**零配置可直接用**（Semantic Scholar 建议配置 Key），
> 不依赖任何付费服务；API Key 只写入本机 `~/.sciforge/config.toml`（0600）或环境变量。

## 一、渠道矩阵（实测）

| 渠道 | 能拿到什么 | 是否需要 Key | 实测结果 |
|---|---|---|---|
| **arXiv** | 预印本元数据 + 摘要 + 全文 PDF；最新 AI 论文 | 否 | ✅ 可用（推荐默认） |
| **daily-arXiv-ai-enhanced** | 每日 arXiv 精选 + DeepSeek 中文增强摘要（tldr/动机/方法/结果/结论） | 否 | ✅ 可用（上游增强文件为空时自动回退普通 JSONL） |
| **OpenAlex** | 跨库元数据（标题/作者/年份/DOI）、开放获取链接 | 否（填邮箱进入礼貌池更稳） | ✅ 可用 |
| **Crossref** | DOI 权威元数据、BibTeX 基础字段 | 否 | ✅ 可用 |
| **HF Papers** | HuggingFace 每日/趋势论文（AI 社区热度） | 否 | ✅ 可用 |
| **Semantic Scholar** | 引用图、摘要、论文详情、推荐 | 建议（匿名易 429） | ⚠️ 匿名 429；配 `S2_API_KEY` 后可用 |

另外：
- **PDF 下载（`paper_fetch`）**：arXiv 直链 → Unpaywall（需要邮箱）→ OpenAlex OA → 本地库；付费墙论文可选接 `instsci`（复用你的机构/WebVPN 浏览器会话，需自行安装）。
- **BibTeX（`paper_cite`）**：arXiv / DOI 元数据生成，自动写 `references.bib` 并去重。
- **精读（`paper_read` / `paper_note`）**：PDF → 章节切分 → 结构化笔记；扫描版需要 OCR。
- **本地文献库**：所有检索结果自动入库（`~/.sciforge/library/`），支持关键词相关度检索、笔记、PDF 缓存。

## 二、需要配置什么（只有两项是可选的）

| 配置项 | 作用 | 不配置的后果 |
|---|---|---|
| `S2_API_KEY` / `[literature] s2_api_key` | Semantic Scholar 配额 | 匿名访问经常 429，改用 arXiv/OpenAlex 即可 |
| 邮箱（`email` / `unpaywall_email`） | Unpaywall 开放获取查询、礼貌池 | 部分非 arXiv 论文无法自动找 OA PDF |

其余（arXiv、daily、OpenAlex、Crossref、HF Papers、PDF 下载、BibTeX）**无需任何 Key**。

## 三、在哪里配置（三种方式，任选）

### 方式 1：网页 GUI（推荐）

打开 `sciforge gui` → **文献** 页 → 顶部「检索设置」面板：

- 联系邮箱 / Unpaywall 邮箱
- Semantic Scholar Key（留空保持不变；勾选可清除）
- S2 Key 环境变量名（默认 `S2_API_KEY`）
- 每日 arXiv 语言（中文 / English）
- 「保存检索设置」立即生效并写入 `~/.sciforge/config.toml`（0600）

面板里同时展示当前可用渠道与「是否需要 Key」。

### 方式 2：CLI

```bash
export S2_API_KEY=...          # 环境变量优先级最高
# 或直接编辑配置
sciforge config path           # 查看配置文件路径
```

### 方式 3：配置文件 `~/.sciforge/config.toml`

```toml
[literature]
email = "you@example.com"
unpaywall_email = "you@example.com"   # 可留空
s2_api_key_env = "S2_API_KEY"         # 读环境变量
# s2_api_key = "..."                  # 也可直接写（本机 0600）
daily_arxiv_language = "Chinese"      # Chinese | English
arxiv_categories = ["cs.CV", "cs.CL", "cs.LG", "cs.AI"]
```

## 四、使用方式

```bash
# 检索（可多来源）
sciforge lit search "diffusion transformer video generation" --source arxiv --limit 20
sciforge lit search "..." --source openalex --source crossref --limit 10

# 每日 arXiv 中文速览
sciforge lit daily --category cs.CV --limit 20

# 下载 PDF + 精读 + 笔记 + BibTeX
sciforge lit fetch 2610.22369
sciforge read 2610.22369 --note
sciforge lit cite 2610.22369

# 本地库
sciforge lit list
```

GUI：**文献** 页直接搜索 / 每日 arXiv / 我的文献库，卡片上可一键「精读 / 入库 / arXiv / PDF」。

## 五、常见问题

| 现象 | 处理 |
|---|---|
| Semantic Scholar 429 | 配 `S2_API_KEY`，或改用 `--source arxiv/openalex` |
| HF Papers 超时 | 网络问题，改用 arXiv/OpenAlex |
| daily 最新日期没有 AI 摘要 | 上游增强文件可能为空，会自动回退普通 JSONL 摘要 |
| 付费论文拿不到 PDF | 配置 `email` 走 Unpaywall；或安装 instsci 复用机构访问 |
| 扫描版 PDF 无文本 | `ocrmypdf input.pdf output.pdf` 后再精读 |
