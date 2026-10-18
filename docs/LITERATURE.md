# 文献获取：渠道、全文与配置

> 状态：2026-10-18 实测。**零配置即可用**；不集成 Sci-Hub 等未授权镜像（见下）。

## 一、检索渠道（实测）

| 渠道 | 能拿到什么 | 需要 Key | 实测 |
|---|---|---|---|
| **arXiv** | 预印本元数据 + 摘要 + 全文 PDF，AI 论文最新最全 | 否 | ✅ 可用（默认） |
| **daily-arXiv-ai-enhanced** | 每日 arXiv 精选 + DeepSeek 中文增强摘要 | 否 | ✅ 可用 |
| **OpenAlex** | 跨库元数据、DOI、开放获取链接 | 否（填邮箱更稳） | ✅ 可用 |
| **Crossref** | DOI 权威元数据、BibTeX 基础字段 | 否 | ✅ 可用 |
| **HF Papers** | HuggingFace 每日/趋势论文 | 否 | ✅ 可用 |

> 按需求已从界面与默认流程中**移除 Semantic Scholar**（匿名 429 严重）。适配器代码保留但不再作为渠道暴露。

## 二、全文获取（只走合法来源）

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | arXiv 直链 | arXiv 论文直接下载 PDF |
| 2 | Unpaywall | 按 DOI 查找**合法开放获取**版本；需配置邮箱 |
| 3 | OpenAlex OA | 开放获取链接兜底 |
| 4 | instsci（可选） | 复用你自己的学校/图书馆 WebVPN / CARSI / EZproxy 会话，访问你**有订阅权限**的论文；需自行安装 |
| 5 | 手动 PDF | 任何你通过合法途径获得的 PDF（本地/网盘/作者主页）都可 `sciforge read <本地路径>` 精读，工具不会追问来源 |

### 为什么没有 Sci-Hub？

Sci-Hub 未经授权分发受版权保护的论文，在包括中国、美国、欧盟在内的多数法域被认定为侵权。
把它做成自动化下载渠道会给使用者与项目带来实际法律风险，因此**不会集成**。
需要付费墙后的论文时，可用上面的合法路径：OA 版本 / 预印本 / 机构订阅（instsci）/
作者主页 / 出版社 API / 图书馆文献传递 / 直接联系作者要 PDF。

## 三、可选配置（只有一项）

| 配置 | 作用 | 不配置的后果 |
|---|---|---|
| 邮箱（`email` / `unpaywall_email`） | Unpaywall 开放获取查询、礼貌池 | 非 arXiv 论文可能找不到合法 OA 全文 |

其余检索（arXiv / daily / OpenAlex / Crossref / HF Papers）、PDF（arXiv）、BibTeX、精读、本地库**全部免配置**。

## 四、在哪里配置

### 方式 1：网页 GUI（推荐）

`sciforge gui` → **文献** 页 → 顶部「检索设置」面板：

- 联系邮箱（Unpaywall / OA 礼貌池）
- Unpaywall 邮箱（可留空则用上面的邮箱）
- 每日 arXiv 语言（中文 / English）
- 面板同时展示检索渠道与全文获取渠道的可用状态

保存即写入 `~/.sciforge/config.toml`（0600），密钥/邮箱不会回传浏览器。

### 方式 2：配置文件 `~/.sciforge/config.toml`

```toml
[literature]
email = "you@example.com"
unpaywall_email = "you@example.com"   # 可留空
daily_arxiv_language = "Chinese"      # Chinese | English
arxiv_categories = ["cs.CV", "cs.CL", "cs.LG", "cs.AI"]
```

### 方式 3：CLI

```bash
sciforge config path          # 查看配置文件
```

## 五、使用方式

```bash
# 检索（可多来源）
sciforge lit search "diffusion transformer video generation" --source arxiv --limit 20
sciforge lit search "..." --source openalex --source crossref --limit 10

# 每日 arXiv 中文速览
sciforge lit daily --category cs.CV --limit 20

# 全文 + 精读 + 笔记 + BibTeX
sciforge lit fetch 2610.22369            # arXiv / Unpaywall / OpenAlex OA
sciforge lit fetch 10.1109/cvpr52733.2024.00616 --instsci   # 可选：机构访问
sciforge read 2610.22369 --note
sciforge read /path/to/legal.pdf --note  # 手动合法获得的 PDF
sciforge lit cite 2610.22369

# 本地库
sciforge lit list
```

GUI：**文献** 页直接搜索 / 每日 arXiv / 我的文献库，卡片上可一键「精读 / 入库 / arXiv / PDF」。

## 六、常见问题

| 现象 | 处理 |
|---|---|
| 某篇付费论文拿不到 PDF | 先看 arXiv/作者主页/预印本；配置邮箱用 Unpaywall；有机构订阅则装 instsci；或手动下载合法 PDF 后 `sciforge read` |
| HF Papers 超时 | 网络问题，改用 arXiv/OpenAlex |
| daily 最新日期没有 AI 摘要 | 上游增强文件可能为空，会自动回退普通 JSONL 摘要 |
| 扫描版 PDF 无文本 | `ocrmypdf input.pdf output.pdf` 后再精读 |
| 想用 Semantic Scholar | 当前版本已移除该渠道；用 arXiv/OpenAlex 代替 |
