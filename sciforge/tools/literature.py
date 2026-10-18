"""文献检索工具（T1）：arXiv / daily-arXiv-ai-enhanced / Semantic Scholar / OpenAlex / HF / Crossref。

架构约定（ARCHITECTURE §4 / §14.1 / §14.6）：

- 工具在模块 import 时通过 ``register_tool(...)`` 自注册；
- 所有网络请求封装成**接受可选 ``httpx.Client`` 的纯函数**（``arxiv_search`` /
  ``daily_arxiv_search`` / ...），便于测试用 ``httpx.MockTransport`` 完全离线；
- 工具层额外支持 ``ctx.extra["http_client"]`` 注入测试客户端；
- 网络/限流失败返回可操作的 :class:`LiteratureError` 或 ``ToolResult.failure``，绝不抛裸异常；
- 检索结果统一写入 :class:`sciforge.library.PaperLibrary`；
- ``add_cli(app)`` 挂 ``sciforge lit ...`` 子命令（§14.6）。

数据源说明：

- arXiv: ``https://export.arxiv.org/api/query``（Atom XML，最稳定）
- daily-arXiv-ai-enhanced: ``raw.githubusercontent.com/dw-dengwei/daily-arXiv-ai-enhanced/data``
  的 ``assets/file-list.txt`` + ``data/<date>_AI_enhanced_<lang>.jsonl``；增强文件为空/缺失时
  自动回退到同日 ``<date>.jsonl``（只有英文摘要、无 AI 增强字段）。
- Semantic Scholar: 无 key 时极易 429，失败会提示配置 ``S2_API_KEY``。
- HuggingFace Papers: 部分地区/网络可能超时，失败会提示改用 arXiv/OpenAlex。
- OpenAlex / Crossref: 礼貌池需 ``mailto``（配置 ``[literature] email``）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import typer
from rich.console import Console

from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool
from sciforge.library import PaperLibrary, PaperRecord, safe_id

# ---------------------------------------------------------------------------
# 端点与常量
# ---------------------------------------------------------------------------

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_ABS = "https://arxiv.org/abs/{arxiv_id}"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_PAPER_API = "https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
S2_FIELDS = (
    "paperId,title,abstract,url,year,venue,publicationDate,authors,externalIds,"
    "openAccessPdf,publicationTypes"
)
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}"
PUBMED_ESEARCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUROPEPMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OPENREVIEW_API = "https://api2.openreview.net/notes/search"
#: Crossref member ID（合法获取 IEEE/ACM/Springer 等出版社的题录元数据，无需订阅）
CROSSREF_PUBLISHERS = {
    "ieee": 263,
    "acm": 320,
    "springer": 297,
    "elsevier": 78,
    "wiley": 311,
    "taylor_francis": 301,
    "jstor": 112,
}
HF_PAPERS_SEARCH = "https://huggingface.co/api/papers/search"
HF_DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
DAILY_REPO_RAW = "https://raw.githubusercontent.com/dw-dengwei/daily-arXiv-ai-enhanced/data"
DAILY_FILE_LIST_URL = f"{DAILY_REPO_RAW}/assets/file-list.txt"
DAILY_DATA_BASE = f"{DAILY_REPO_RAW}/data"

DEFAULT_TIMEOUT = 30.0
MAX_LIMIT = 100
LOCAL_TEXT_SUFFIXES = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}

SOURCE_ALIASES = {
    "arxiv": "arxiv",
    "s2": "semantic_scholar",
    "semantic_scholar": "semantic_scholar",
    "semanticscholar": "semantic_scholar",
    "openalex": "openalex",
    "hf": "hf_papers",
    "hf_papers": "hf_papers",
    "huggingface": "hf_papers",
    "daily": "daily",
    "daily_arxiv": "daily",
    "crossref": "crossref",
    "pubmed": "pubmed",
    "pmid": "pubmed",
    "europe_pmc": "europe_pmc",
    "europepmc": "europe_pmc",
    "epmc": "europe_pmc",
    "openreview": "openreview",
}

console = Console()


class LiteratureError(RuntimeError):
    """文献检索/下载失败。消息面向用户，包含可操作的修复建议。"""


# ---------------------------------------------------------------------------
# 通用网络与文本工具
# ---------------------------------------------------------------------------


def _lit_settings(settings: Any) -> Any:
    return getattr(settings, "literature", None) if settings is not None else None


def _request_timeout(settings: Any = None) -> float:
    value = getattr(_lit_settings(settings), "request_timeout", None)
    try:
        return float(value) if value else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def _user_agent(settings: Any = None) -> str:
    agent = getattr(_lit_settings(settings), "user_agent", None)
    return str(agent or "SciForge/0.1 (+https://github.com/sddzlsc/SciForge)")


def _contact_email(settings: Any = None) -> str:
    cfg = _lit_settings(settings)
    return str(getattr(cfg, "email", None) or getattr(cfg, "unpaywall_email", None) or "").strip()


def _headers(settings: Any = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": _user_agent(settings),
        "Accept": "application/json, application/atom+xml, text/plain, */*",
    }
    if extra:
        headers.update({key: value for key, value in extra.items() if value})
    return headers


@contextmanager
def open_client(
    client: httpx.Client | None,
    *,
    settings: Any = None,
    timeout: float | None = None,
) -> Iterator[httpx.Client]:
    """复用外部客户端；未提供时创建一个带超时/UA 的临时客户端。"""
    if client is not None:
        yield client
        return
    with httpx.Client(
        timeout=timeout or _request_timeout(settings),
        headers=_headers(settings),
        follow_redirects=True,
    ) as owned:
        yield owned


def _get(
    client: httpx.Client,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """GET 并统一封装网络异常（消息里带可操作建议）。"""
    try:
        return client.get(url, params=params, headers=headers or None)
    except httpx.HTTPError as exc:
        raise LiteratureError(
            f"{source} 网络请求失败（{type(exc).__name__}: {exc}）。"
            "请检查网络/代理后重试；arXiv、OpenAlex、Crossref 通常比 S2/HF 更稳定。"
        ) from exc


def _require_status(resp: httpx.Response, source: str, *, hints: str = "") -> None:
    if resp.status_code == 200:
        return
    detail = ""
    try:
        detail = resp.text[:300].replace("\n", " ")
    except Exception:  # noqa: BLE001 - 响应体读取失败不影响错误信息
        detail = ""
    message = f"{source} 返回 HTTP {resp.status_code}"
    if detail:
        message += f"：{detail}"
    if hints:
        message += f"\n{hints}"
    raise LiteratureError(message)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = re.sub(r"^(?:authors?|作者)\s*[:：]\s*", "", value.strip(), flags=re.IGNORECASE)
        return [part.strip() for part in re.split(r"\s*(?:,|;|、|\band\b)\s*", text) if part.strip()]
    if isinstance(value, dict):
        name = value.get("name") or value.get("display_name") or ""
        return [str(name).strip()] if str(name).strip() else []
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("display_name") or ""
            else:
                name = item
            name = str(name or "").strip()
            if name:
                result.append(name)
        return result
    return [str(value).strip()] if str(value).strip() else []


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _shorten(text: str, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _first_sentences(text: str, count: int = 2) -> str:
    text = _clean(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？.!?])\s+", text)
    return " ".join(parts[:count]).strip()


def _normalize_date(value: Any) -> str | None:
    """把 20260907 / 2026/09/07 等统一成 YYYY-MM-DD；无法识别返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def _resolved_date(value: Any) -> str | None:
    text = str(value or "").strip()
    return _normalize_date(text) or (text[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]) else None)


def _dedupe_records(records: list[PaperRecord]) -> list[PaperRecord]:
    deduped: dict[str, PaperRecord] = {}
    for record in records:
        if not record.id:
            continue
        existing = deduped.get(record.id)
        if existing is None:
            deduped[record.id] = record
        else:
            existing.merge_missing(record)
    return list(deduped.values())


def _interleave(groups: list[list[PaperRecord]], limit: int) -> list[PaperRecord]:
    """多来源结果交替合并（保持多样性），按 id 去重，最多 ``limit`` 条。"""
    output: list[PaperRecord] = []
    seen: set[str] = set()
    index = 0
    while len(output) < limit and any(index < len(group) for group in groups):
        for group in groups:
            if index >= len(group):
                continue
            record = group[index]
            if record.id and record.id not in seen:
                seen.add(record.id)
                output.append(record)
            if len(output) >= limit:
                break
        index += 1
    return output


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", re.IGNORECASE)
_NEW_ARXIV_RE = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?")
_OLD_ARXIV_RE = re.compile(r"[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?", re.IGNORECASE)
_QUERY_TERM_RE = re.compile(r'"[^"]+"|\S+')
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def normalize_arxiv_id(value: str) -> str | None:
    """从 arXiv ID / abs URL / pdf URL 中提取去版本号的 ID；非 arXiv 返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    match = _ARXIV_URL_RE.search(text)
    if match:
        text = match.group(1)
    text = re.sub(r"^arxiv:", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE).strip()
    if _NEW_ARXIV_RE.fullmatch(text) or _OLD_ARXIV_RE.fullmatch(text):
        return re.sub(r"v\d+$", "", text)
    return None


def _since_arxiv_datetime(since: str | None) -> str | None:
    """把 since（YYYY-MM-DD / YYYY / 7d / 30d / 1y）转成 arXiv 的 YYYYMMDDHHMM。"""
    text = str(since or "").strip()
    if not text:
        return None
    relative = re.fullmatch(r"(\d+)\s*([dmy])", text, flags=re.IGNORECASE)
    if relative:
        amount = int(relative.group(1))
        days = amount * {"d": 1, "m": 30, "y": 365}[relative.group(2).lower()]
        moment = datetime.now() - timedelta(days=days)
        return moment.strftime("%Y%m%d") + "0000"
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").strftime("%Y%m%d") + "0000"
        except ValueError:
            return None
    if len(digits) == 4:
        return f"{digits}01010000"
    return None


def build_arxiv_query(query: str = "", *, category: str | None = None, since: str | None = None) -> str:
    """把自然语言查询拼成 arXiv Atom API 的 ``search_query``。

    例：``diffusion transformer`` + category=cs.CV
    → ``all:diffusion AND all:transformer AND cat:cs.CV``。
    已经带字段前缀（如 ``ti:`` / ``au:`` / ``cat:``）的查询原样保留。
    """
    text = str(query or "").strip()
    parts: list[str] = []
    if text:
        if re.search(r"(?:^|[\s(])(?:all|ti|au|abs|cat|co|jr|rn|id):", text):
            parts.append(text)
        else:
            atoms: list[str] = []
            for term in _QUERY_TERM_RE.findall(text):
                term = term.strip()
                if not term or term.upper() in {"AND", "OR", "NOT", "ANDNOT"}:
                    continue
                atoms.append(f"all:{term}")
            if atoms:
                parts.append(" AND ".join(atoms))
            else:
                parts.append(f'all:"{text}"')
    if category and str(category).strip():
        parts.append(f"cat:{str(category).strip()}")
    start = _since_arxiv_datetime(since)
    if start:
        parts.append(f"submittedDate:[{start} TO 209912312359]")
    return " AND ".join(parts)


def parse_arxiv_atom(xml_text: str) -> list[PaperRecord]:
    """解析 arXiv Atom XML 为 PaperRecord 列表（纯函数，便于离线测试）。"""
    raw = str(xml_text or "").strip()
    if not raw:
        raise LiteratureError("arXiv 返回空响应，请稍后重试或改用 OpenAlex/Crossref。")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise LiteratureError(
            f"arXiv 返回的 XML 无法解析（{exc}）。通常是临时服务异常，请稍后重试。"
        ) from exc
    records: list[PaperRecord] = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        raw_id = _clean(entry.findtext("atom:id", default="", namespaces=ARXIV_NS))
        arxiv_id = normalize_arxiv_id(raw_id) or _clean(raw_id.rsplit("/", 1)[-1])
        title = _clean(entry.findtext("atom:title", default="", namespaces=ARXIV_NS))
        if not title:
            continue
        abstract = _clean(entry.findtext("atom:summary", default="", namespaces=ARXIV_NS))
        published = _clean(entry.findtext("atom:published", default="", namespaces=ARXIV_NS))[:10]
        authors = [
            _clean(author.findtext("atom:name", default="", namespaces=ARXIV_NS))
            for author in entry.findall("atom:author", ARXIV_NS)
        ]
        categories: list[str] = []
        primary = entry.find("arxiv:primary_category", ARXIV_NS)
        if primary is not None and primary.get("term"):
            categories.append(str(primary.get("term")))
        for category_node in entry.findall("atom:category", ARXIV_NS):
            term = category_node.get("term")
            if term and term not in categories:
                categories.append(term)
        url = ""
        pdf_url = ""
        for link in entry.findall("atom:link", ARXIV_NS):
            href = link.get("href") or ""
            if not href:
                continue
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = pdf_url or href
            elif link.get("rel") == "alternate" and not url:
                url = href
        if arxiv_id:
            url = url or ARXIV_ABS.format(arxiv_id=arxiv_id)
            pdf_url = pdf_url or ARXIV_PDF.format(arxiv_id=arxiv_id)
        venue = _clean(entry.findtext("arxiv:journal_ref", default="", namespaces=ARXIV_NS))
        records.append(
            PaperRecord(
                id=arxiv_id or safe_id(title),
                title=title,
                authors=authors,
                abstract=abstract,
                url=url,
                pdf_url=pdf_url,
                published=published,
                venue=venue,
                categories=categories,
                source="arxiv",
                tags=["arxiv"],
            )
        )
    return records


def arxiv_search(
    query: str,
    *,
    limit: int = 20,
    since: str | None = None,
    category: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """arXiv 检索（Atom API）。失败抛 :class:`LiteratureError`。"""
    search_query = build_arxiv_query(query, category=category, since=since)
    if not search_query:
        raise LiteratureError("arXiv 检索需要 query 或 category 至少一个。")
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max(1, min(int(limit or 20), MAX_LIMIT)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    with open_client(client, settings=settings) as http:
        resp = _get(http, ARXIV_API, source="arXiv", params=params)
    if resp.status_code != 200:
        raise LiteratureError(
            f"arXiv 检索失败（HTTP {resp.status_code}）。arXiv 有频率限制，"
            "请降低频率并在检索条件中加 category（如 cs.CV）缩小范围。"
        )
    return parse_arxiv_atom(resp.text)


def arxiv_get_by_ids(
    ids: list[str],
    *,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """按 arXiv ID 批量取元数据（用于 BibTeX / PDF 下载补全）。"""
    normalized = [arxiv_id for arxiv_id in (normalize_arxiv_id(i) for i in ids) if arxiv_id]
    if not normalized:
        raise LiteratureError("没有可用的 arXiv ID（示例：2401.00001）。")
    params = {
        "id_list": ",".join(normalized),
        "start": 0,
        "max_results": max(1, min(len(normalized), 50)),
    }
    with open_client(client, settings=settings) as http:
        resp = _get(http, ARXIV_API, source="arXiv", params=params)
    _require_status(resp, "arXiv 元数据接口", hints="请稍后重试，或改用 DOI 检索。")
    return parse_arxiv_atom(resp.text)


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


def _reconstruct_abstract(inverted: dict[str, Any] | None) -> str:
    """OpenAlex 的 ``abstract_inverted_index`` → 可读摘要。"""
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted.items():
        for index in indexes or []:
            try:
                positions.append((int(index), str(word)))
            except (TypeError, ValueError):
                continue
    positions.sort(key=lambda item: item[0])
    return _clean(" ".join(word for _, word in positions))


def _openalex_record(work: dict[str, Any]) -> PaperRecord | None:
    title = _clean(work.get("display_name") or work.get("title") or "")
    if not title:
        return None
    doi = _clean(work.get("doi")).replace("https://doi.org/", "")
    openalex_id = _clean(work.get("id")).rsplit("/", 1)[-1]
    primary = work.get("primary_location") or {}
    best_oa = work.get("best_oa_location") or {}
    source_meta = primary.get("source") or {}
    authors = [
        _clean((authorship.get("author") or {}).get("display_name"))
        for authorship in work.get("authorships") or []
    ]
    categories = [
        _clean(concept.get("display_name"))
        for concept in work.get("concepts") or []
        if concept.get("display_name")
    ][:5]
    year = work.get("publication_year")
    published = _clean(work.get("publication_date")) or (f"{year}-01-01" if year else "")
    url = _clean(primary.get("landing_page_url")) or (f"https://doi.org/{doi}" if doi else "")
    return PaperRecord(
        id=doi or openalex_id or safe_id(title),
        title=title,
        authors=authors,
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        url=url,
        pdf_url=_clean(best_oa.get("pdf_url")) or _clean(primary.get("pdf_url")),
        published=published[:10],
        venue=_clean(source_meta.get("display_name")),
        categories=categories,
        source="openalex",
        tags=["openalex"],
    )


def _openalex_email_params(settings: Any) -> dict[str, str]:
    email = _contact_email(settings)
    return {"mailto": email} if email else {}


def openalex_search(
    query: str,
    *,
    limit: int = 20,
    since: str | None = None,
    category: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """OpenAlex 检索（无需 API key，支持 OA PDF 链接；礼貌池建议配置 email）。"""
    params: dict[str, Any] = {
        "search": str(query or "").strip(),
        "per-page": max(1, min(int(limit or 20), 200)),
        "sort": "relevance_score:desc",
    }
    params.update(_openalex_email_params(settings))
    filters: list[str] = []
    since_date = _resolved_date(since)
    if since_date:
        filters.append(f"from_publication_date:{since_date}")
    if category and str(category).strip():
        filters.append(f"primary_topic.field.id:{str(category).strip()}")
    if filters:
        params["filter"] = ",".join(filters)
    with open_client(client, settings=settings) as http:
        resp = _get(http, OPENALEX_API, source="OpenAlex", params=params)
    _require_status(resp, "OpenAlex", hints="OpenAlex 免费额度有限，请稍后重试或改用 arXiv。")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LiteratureError(f"OpenAlex 返回的不是合法 JSON：{exc}") from exc
    records = [_openalex_record(work) for work in payload.get("results") or []]
    return [record for record in records if record is not None]


def _openalex_work_by_doi(
    doi: str,
    *,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> PaperRecord | None:
    params: dict[str, Any] = {"filter": f"doi:{doi}", "per-page": 1}
    params.update(_openalex_email_params(settings))
    with open_client(client, settings=settings) as http:
        resp = _get(http, OPENALEX_API, source="OpenAlex", params=params)
    if resp.status_code != 200:
        raise LiteratureError(f"OpenAlex DOI 查询失败（HTTP {resp.status_code}）：{doi}")
    try:
        results = resp.json().get("results") or []
    except ValueError:
        return None
    return _openalex_record(results[0]) if results else None


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------


def _s2_api_key(settings: Any = None) -> str:
    cfg = _lit_settings(settings)
    env_name = str(getattr(cfg, "s2_api_key_env", None) or "S2_API_KEY")
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    return str(getattr(cfg, "s2_api_key", "") or "").strip()


def _s2_record(paper: dict[str, Any]) -> PaperRecord | None:
    title = _clean(paper.get("title"))
    if not title:
        return None
    external = paper.get("externalIds") or {}
    arxiv_id = normalize_arxiv_id(_clean(external.get("ArXiv")))
    doi = _clean(external.get("DOI")) or normalize_doi(_clean(paper.get("url"))) or ""
    paper_id = _clean(paper.get("paperId"))
    record_id = arxiv_id or doi or paper_id
    if not record_id:
        return None
    open_access = paper.get("openAccessPdf") or {}
    return PaperRecord(
        id=record_id,
        title=title,
        authors=_as_list(paper.get("authors")),
        abstract=_clean(paper.get("abstract")),
        url=_clean(paper.get("url")),
        pdf_url=_clean(open_access.get("url")),
        published=_clean(paper.get("publicationDate"))[:10],
        venue=_clean(paper.get("venue")),
        categories=[],
        source="s2",
        tags=["s2"],
    )


def semantic_scholar_search(
    query: str,
    *,
    limit: int = 20,
    since: str | None = None,
    category: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """Semantic Scholar Graph API 检索（12k 篇以内免费；无 key 常 429）。"""
    params: dict[str, Any] = {
        "query": str(query or "").strip(),
        "limit": max(1, min(int(limit or 20), 100)),
        "fields": S2_FIELDS,
    }
    since_date = _resolved_date(since)
    if since_date:
        params["year"] = f"{since_date[:4]}-"
    headers = _headers(settings)
    api_key = _s2_api_key(settings)
    if api_key:
        headers["x-api-key"] = api_key
    with open_client(client, settings=settings) as http:
        resp = _get(http, S2_API, source="Semantic Scholar", params=params, headers=headers)
    if resp.status_code == 429:
        hint = (
            "Semantic Scholar 匿名访问被限流（HTTP 429）。修复方式："
            "1) 申请免费 key（https://www.semanticscholar.org/product/api）后 "
            "export S2_API_KEY=... 或写进 ~/.sciforge/config.toml 的 [literature] s2_api_key_env；"
            "2) 稍后重试；3) 改用 --source arxiv / openalex（更稳定）。"
        )
        raise LiteratureError(hint)
    if resp.status_code in (401, 403):
        raise LiteratureError(
            f"Semantic Scholar 鉴权失败（HTTP {resp.status_code}）。"
            "请确认 S2_API_KEY 是否有效，或改用 --source arxiv / openalex。"
        )
    _require_status(resp, "Semantic Scholar", hints="可改用 --source arxiv / openalex。")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LiteratureError(f"Semantic Scholar 返回的不是合法 JSON：{exc}") from exc
    records = [_s2_record(item) for item in payload.get("data") or []]
    return [record for record in records if record is not None]


def semantic_scholar_get(
    identifier: str,
    *,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> PaperRecord | None:
    """按 S2 paperId / DOI / arXiv ID 取单篇元数据（paper_cite 的 S2 兜底）。"""
    raw = str(identifier or "").strip()
    if not raw:
        return None
    candidates = [raw]
    doi = normalize_doi(raw)
    arxiv_id = normalize_arxiv_id(raw)
    if doi:
        candidates.append(f"DOI:{doi}")
    if arxiv_id:
        candidates.append(f"ARXIV:{arxiv_id}")
    headers = _headers(settings)
    api_key = _s2_api_key(settings)
    if api_key:
        headers["x-api-key"] = api_key
    with open_client(client, settings=settings) as http:
        for candidate in candidates:
            url = S2_PAPER_API.format(paper_id=quote(candidate, safe=""))
            resp = _get(http, url, source="Semantic Scholar", headers=headers)
            if resp.status_code == 404:
                continue
            if resp.status_code == 429:
                raise LiteratureError(
                    "Semantic Scholar 限流（HTTP 429），无法补全引用元数据。"
                    "请稍后重试或设置 S2_API_KEY。"
                )
            _require_status(resp, "Semantic Scholar", hints="可改用 arXiv ID/DOI 生成引用。")
            try:
                payload = resp.json()
            except ValueError:
                continue
            record = _s2_record(payload) if isinstance(payload, dict) else None
            if record is not None:
                return record
    return None


# ---------------------------------------------------------------------------
# HuggingFace Papers
# ---------------------------------------------------------------------------


def _hf_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("papers", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _hf_record(item: dict[str, Any]) -> PaperRecord | None:
    paper = item.get("paper") if isinstance(item.get("paper"), dict) else item
    paper_id = _clean(paper.get("id") or paper.get("paperId"))
    title = _clean(paper.get("title"))
    if not title:
        return None
    authors = _as_list(paper.get("authors"))
    summary = _clean(paper.get("summary") or paper.get("abstract"))
    arxiv_id = normalize_arxiv_id(paper_id)
    url = f"https://huggingface.co/papers/{paper_id}" if paper_id else ""
    return PaperRecord(
        id=arxiv_id or paper_id or safe_id(title),
        title=title,
        authors=authors,
        abstract=summary,
        url=url,
        pdf_url=ARXIV_PDF.format(arxiv_id=arxiv_id) if arxiv_id else _clean(paper.get("pdf")),
        published=_clean(item.get("publishedAt") or paper.get("publishedAt"))[:10],
        venue="",
        categories=[],
        source="hf",
        tags=["hf"],
    )


def hf_papers_search(
    query: str = "",
    *,
    limit: int = 20,
    daily: bool = False,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """HuggingFace Papers 检索；``daily=True`` 或 query 为空时取 daily/trending。"""
    capped = max(1, min(int(limit or 20), 100))
    use_daily = daily or not str(query or "").strip()
    url = HF_DAILY_PAPERS if use_daily else HF_PAPERS_SEARCH
    params: dict[str, Any] = {"limit": capped}
    if not use_daily:
        params = {"q": str(query).strip()}
    with open_client(client, settings=settings, timeout=_request_timeout(settings)) as http:
        resp = _get(http, url, source="HuggingFace Papers", params=params)
    if resp.status_code == 404:
        raise LiteratureError(
            "HuggingFace Papers 接口返回 404（endpoint 可能已变更）。"
            "请改用 --source arxiv / openalex。"
        )
    _require_status(
        resp,
        "HuggingFace Papers",
        hints="HF 在部分网络下会超时/被墙，请改用 --source arxiv / openalex / daily。",
    )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LiteratureError(f"HuggingFace Papers 返回的不是合法 JSON：{exc}") from exc
    records = [_hf_record(item) for item in _hf_items(payload)]
    return [record for record in records if record is not None][:capped]


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------


def _crossref_record(item: dict[str, Any]) -> PaperRecord | None:
    title_value = item.get("title") or []
    title = _clean(title_value[0] if isinstance(title_value, list) and title_value else title_value)
    if not title:
        return None
    doi = _clean(item.get("DOI"))
    authors: list[str] = []
    for author in item.get("author") or []:
        name = " ".join(part for part in (author.get("given"), author.get("family")) if part)
        name = _clean(name or author.get("name"))
        if name:
            authors.append(name)
    published = ""
    issued = item.get("issued") or {}
    date_parts = (issued.get("date-parts") or [[]])[0] if isinstance(issued, dict) else []
    if date_parts and date_parts[0]:
        published = "-".join(str(part).zfill(2) for part in date_parts[:3])
    venue_value = item.get("container-title") or []
    venue = _clean(venue_value[0] if isinstance(venue_value, list) and venue_value else venue_value)
    pdf_url = ""
    for link in item.get("link") or []:
        if isinstance(link, dict) and link.get("content-type") == "application/pdf" and link.get("URL"):
            pdf_url = str(link["URL"])
            break
    abstract = _clean(re.sub(r"<[^>]+>", " ", str(item.get("abstract") or "")))
    return PaperRecord(
        id=doi or safe_id(title),
        title=title,
        authors=authors,
        abstract=abstract,
        url=_clean(item.get("URL")) or (f"https://doi.org/{doi}" if doi else ""),
        pdf_url=pdf_url,
        published=published,
        venue=venue,
        categories=[],
        source="crossref",
        tags=["crossref"],
    )


def crossref_search(
    query: str,
    *,
    limit: int = 20,
    since: str | None = None,
    publisher: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """Crossref 检索（DOI 元数据权威，摘要/PDF 链接不一定齐全）。

    ``publisher`` 可用 IEEE / ACM / Springer / Elsevier / Wiley / Taylor & Francis / JSTOR
    的 member 过滤，免费拿到这些出版社的题录（全文仍需订阅或 OA）。
    """
    params: dict[str, Any] = {
        "query.bibliographic": str(query or "").strip(),
        "rows": max(1, min(int(limit or 20), 100)),
    }
    filters: list[str] = []
    since_date = _resolved_date(since)
    if since_date:
        filters.append(f"from-pub-date:{since_date}")
    if publisher:
        key = re.sub(r"[\s\-]+", "_", str(publisher).strip().lower())
        member = CROSSREF_PUBLISHERS.get(key)
        if member is None:
            raise LiteratureError(
                f"未知出版社过滤：{publisher}；可用：{', '.join(sorted(CROSSREF_PUBLISHERS))}"
            )
        filters.append(f"member:{member}")
    if filters:
        params["filter"] = ",".join(filters)
    email = _contact_email(settings)
    if email:
        params["mailto"] = email
    with open_client(client, settings=settings) as http:
        resp = _get(http, CROSSREF_API, source="Crossref", params=params)
    _require_status(resp, "Crossref", hints="Crossref 偶发限流，请稍后重试。")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LiteratureError(f"Crossref 返回的不是合法 JSON：{exc}") from exc
    items = (payload.get("message") or {}).get("items") or []
    records = [_crossref_record(item) for item in items if isinstance(item, dict)]
    return [record for record in records if record is not None]


# ---------------------------------------------------------------------------
# PubMed / Europe PMC（生命科学、医学；免费 E-utilities / REST API）
# ---------------------------------------------------------------------------


def parse_pubmed_xml(xml_text: str, *, limit: int | None = None) -> list[PaperRecord]:
    """解析 PubMed efetch 返回的 XML（免费、无需 API key）。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LiteratureError(f"PubMed XML 解析失败：{exc}") from exc
    records: list[PaperRecord] = []
    for article in root.findall(".//PubmedArticle"):
        citation = article.find("MedlineCitation")
        if citation is None:
            continue
        pmid = _clean(citation.findtext("PMID"))
        art = citation.find("Article")
        if art is None:
            continue
        title_node = art.find("ArticleTitle")
        title = _clean("".join(title_node.itertext()).strip()) if title_node is not None else ""
        abstract = " ".join(
            "".join(node.itertext()).strip() for node in art.findall("Abstract/AbstractText")
        ).strip()
        authors: list[str] = []
        for author in art.findall("AuthorList/Author"):
            collective = _clean(author.findtext("CollectiveName"))
            if collective:
                authors.append(collective)
                continue
            last = _clean(author.findtext("LastName"))
            fore = _clean(author.findtext("ForeName"))
            if last or fore:
                authors.append((fore + " " + last).strip())
        journal = _clean(art.findtext("Journal/Title")) or _clean(art.findtext("Journal/ISOAbbreviation"))
        year = _clean(art.findtext("Journal/JournalIssue/PubDate/Year"))
        if not year:
            medline_date = _clean(art.findtext("Journal/JournalIssue/PubDate/MedlineDate"))
            match = re.search(r"(\d{4})", medline_date or "")
            year = match.group(1) if match else ""
        doi = ""
        pmc = ""
        for article_id in article.findall("PubmedData/ArticleIdList/ArticleId"):
            id_type = (article_id.get("IdType") or "").lower()
            if id_type == "doi":
                doi = _clean(article_id.text)
            elif id_type == "pmc":
                pmc = _clean(article_id.text)
        records.append(
            PaperRecord(
                id=doi or (f"PMID:{pmid}" if pmid else safe_id(title)),
                title=title or f"PMID {pmid}",
                authors=authors,
                abstract=abstract,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                pdf_url=f"https://europepmc.org/articles/{pmc}?pdf=render" if pmc else "",
                published=year,
                venue=journal,
                categories=["PubMed"],
                source="pubmed",
                tags=["pubmed", "biomed"] + (["pmc"] if pmc else []),
            )
        )
        if limit and len(records) >= limit:
            break
    return records


def pubmed_search(
    query: str,
    *,
    limit: int = 20,
    since: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """PubMed 检索（NCBI E-utilities；免费，建议配置 email 提升配额）。"""
    term = str(query or "").strip()
    if not term:
        raise LiteratureError("PubMed 检索需要关键词。")
    since_date = _resolved_date(since)
    if since_date:
        term = f"({term}) AND (\"{since_date}\"[Date - Publication] : \"3000\"[Date - Publication])"
    email = _contact_email(settings)
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": max(1, min(int(limit or 20), 100)),
        "sort": "relevance",
    }
    if email:
        params["email"] = email
    with open_client(client, settings=settings) as http:
        resp = _get(http, PUBMED_ESEARCH_API, source="PubMed", params=params)
        _require_status(resp, "PubMed", hints="PubMed 有频率限制（3 req/s），可稍后重试。")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise LiteratureError(f"PubMed 返回的不是合法 JSON：{exc}") from exc
        ids = (payload.get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []
        fetch_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
        if email:
            fetch_params["email"] = email
        fetched = _get(http, PUBMED_EFETCH_API, source="PubMed", params=fetch_params)
    _require_status(fetched, "PubMed efetch")
    return parse_pubmed_xml(fetched.text, limit=limit)


def _europe_pmc_record(item: dict[str, Any]) -> PaperRecord | None:
    title = _clean(item.get("title"))
    if not title:
        return None
    doi = _clean(item.get("doi"))
    source_id = _clean(item.get("id"))
    pmcid = _clean(item.get("pmcid"))
    authors = [
        part.strip()
        for part in re.split(r"[,;]", _clean(item.get("authorString")))
        if part.strip()
    ]
    pdf_url = ""
    for link in (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        if not isinstance(link, dict):
            continue
        if str(link.get("availability") or "").lower() == "open access":
            pdf_url = _clean(link.get("url"))
            if pdf_url:
                break
    year = _clean(item.get("pubYear"))
    published = _clean(item.get("firstPublicationDate")) or year
    record_id = doi or (f"EPMC:{source_id}" if source_id else safe_id(title))
    tags = ["europepmc"]
    if str(item.get("source") or "").lower() in ("ppr", "preprint"):
        tags.append("preprint")
    return PaperRecord(
        id=record_id,
        title=title,
        authors=authors,
        abstract=_clean(item.get("abstractText")),
        url=f"https://doi.org/{doi}" if doi else (
            f"https://europepmc.org/article/{item.get('source') or 'MED'}/{source_id}"
        ),
        pdf_url=pdf_url or (
            f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else ""
        ),
        published=published[:10],
        venue=_clean(item.get("journalTitle") or item.get("bookOrReportDetails", {}).get("publisher")),
        categories=[_clean(item.get("pubType"))] if _clean(item.get("pubType")) else ["Europe PMC"],
        source="europe_pmc",
        tags=tags,
    )


def europe_pmc_search(
    query: str,
    *,
    limit: int = 20,
    since: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """Europe PMC 检索（免费 REST；覆盖 PubMed、PMC、预印本等）。"""
    term = str(query or "").strip()
    if not term:
        raise LiteratureError("Europe PMC 检索需要关键词。")
    since_date = _resolved_date(since)
    if since_date:
        term = f"({term}) AND (FIRST_PDATE:[{since_date} TO 3000-12-31])"
    params = {
        "query": term,
        "format": "json",
        "pageSize": max(1, min(int(limit or 20), 100)),
        "resultType": "core",
        "sort": "CITED desc",
    }
    email = _contact_email(settings)
    if email:
        params["email"] = email
    with open_client(client, settings=settings) as http:
        resp = _get(http, EUROPEPMC_API, source="Europe PMC", params=params)
    _require_status(resp, "Europe PMC")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LiteratureError(f"Europe PMC 返回的不是合法 JSON：{exc}") from exc
    items = (payload.get("resultList") or {}).get("result") or []
    records = [_europe_pmc_record(item) for item in items if isinstance(item, dict)]
    return [record for record in records if record is not None]


# ---------------------------------------------------------------------------
# OpenReview（ICLR / NeurIPS 等会议公开评审与论文）
# ---------------------------------------------------------------------------


def _openreview_value(content: dict[str, Any], key: str) -> Any:
    value = content.get(key)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _openreview_record(note: dict[str, Any]) -> PaperRecord | None:
    content = note.get("content") or {}
    if not isinstance(content, dict):
        return None
    title = _clean(_openreview_value(content, "title"))
    if not title:
        return None
    forum = _clean(note.get("forum") or note.get("id"))
    authors = _openreview_value(content, "authors") or []
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.split(",") if part.strip()]
    venue = _clean(_openreview_value(content, "venue") or _openreview_value(content, "venueid"))
    timestamp = note.get("pdate") or note.get("cdate") or note.get("tcdate")
    published = ""
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        published = datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d")
    keywords = _openreview_value(content, "keywords") or []
    if isinstance(keywords, str):
        keywords = [part.strip() for part in keywords.split(",") if part.strip()]
    return PaperRecord(
        id=f"openreview:{forum}" if forum else safe_id(title),
        title=title,
        authors=[str(author) for author in authors if str(author).strip()],
        abstract=_clean(_openreview_value(content, "abstract")),
        url=f"https://openreview.net/forum?id={forum}" if forum else "",
        pdf_url=f"https://openreview.net/pdf?id={forum}" if forum else "",
        published=published,
        venue=venue,
        categories=[str(item) for item in keywords][:5],
        source="openreview",
        tags=["openreview", "conference"],
    )


def openreview_search(
    query: str,
    *,
    limit: int = 20,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """OpenReview 检索（免费公开 API；适合 ICLR/NeurIPS 等最新会议论文）。"""
    term = str(query or "").strip()
    if not term:
        raise LiteratureError("OpenReview 检索需要关键词。")
    params = {
        "term": term,
        "limit": max(1, min(int(limit or 20), 100)),
        "type": "terms",
        "content": "all",
        "group": "all",
        "source": "forum",
    }
    with open_client(client, settings=settings) as http:
        resp = _get(http, OPENREVIEW_API, source="OpenReview", params=params)
    _require_status(resp, "OpenReview", hints="OpenReview API 偶发 5xx，可稍后重试。")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LiteratureError(f"OpenReview 返回的不是合法 JSON：{exc}") from exc
    notes = payload.get("notes") or []
    records: list[PaperRecord] = []
    seen: set[str] = set()
    for note in notes:
        if not isinstance(note, dict):
            continue
        record = _openreview_record(note)
        if record is None or record.id in seen:
            continue
        seen.add(record.id)
        records.append(record)
        if len(records) >= limit:
            break
    return records


# ---------------------------------------------------------------------------
# daily-arXiv-ai-enhanced
# ---------------------------------------------------------------------------


def _file_date(filename: str) -> str | None:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", filename)
    return match.group(1) if match else None


def fetch_daily_file_list(
    *,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[str]:
    """获取 daily-arXiv-ai-enhanced 仓库的文件清单（每行一个 jsonl 文件名）。"""
    with open_client(client, settings=settings) as http:
        resp = _get(http, DAILY_FILE_LIST_URL, source="daily-arXiv-ai-enhanced")
    _require_status(
        resp,
        "daily-arXiv-ai-enhanced 文件列表",
        hints="该数据源依赖 GitHub raw 访问；若网络不可用，请改用 --source arxiv。",
    )
    files = [line.strip() for line in resp.text.splitlines() if line.strip().endswith(".jsonl")]
    if not files:
        raise LiteratureError(
            "daily-arXiv-ai-enhanced 文件列表为空（可能上游仓库结构变更）。"
            "请改用 --source arxiv，或稍后重试。"
        )
    return files


def pick_daily_file(
    files: list[str],
    *,
    date: str | None = None,
    language: str = "Chinese",
) -> str:
    """从文件清单里选择最合适的 jsonl：优先 AI 增强的目标语言，回退中文/plain。"""
    lang = "English" if str(language or "").strip().lower().startswith("en") else "Chinese"
    if date:
        normalized = _normalize_date(date)
        if normalized:
            candidates = [
                f"{normalized}_AI_enhanced_{lang}.jsonl",
                f"{normalized}_AI_enhanced_Chinese.jsonl",
                f"{normalized}.jsonl",
            ]
            for candidate in candidates:
                if candidate in files:
                    return candidate
            return candidates[0]
    enhanced = [name for name in files if "_AI_enhanced_" in name]
    matching = [name for name in enhanced if name.endswith(f"_AI_enhanced_{lang}.jsonl")]
    pool = matching or enhanced or files
    return max(pool, key=lambda name: _file_date(name) or "")


def _daily_payload_candidates(filename: str) -> list[str]:
    candidates = [filename]
    enhanced_match = re.match(r"(\d{4}-\d{2}-\d{2})_AI_enhanced_[A-Za-z]+\.jsonl$", filename)
    if enhanced_match:
        candidates.append(f"{enhanced_match.group(1)}.jsonl")
    return candidates


def _fetch_daily_payload(
    filename: str,
    *,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> tuple[str, str]:
    """下载 daily jsonl 文本；增强文件为空/404 时自动回退 plain 文件。"""
    errors: list[str] = []
    for candidate in _daily_payload_candidates(filename):
        with open_client(client, settings=settings) as http:
            resp = _get(http, f"{DAILY_DATA_BASE}/{candidate}", source="daily-arXiv-ai-enhanced")
        if resp.status_code == 404:
            errors.append(f"{candidate}: HTTP 404")
            continue
        if resp.status_code != 200:
            errors.append(f"{candidate}: HTTP {resp.status_code}")
            continue
        text = resp.text.strip()
        if not text:
            errors.append(f"{candidate}: 内容为空（该日期可能尚未生成 AI 增强版）")
            continue
        return text, candidate
    raise LiteratureError(
        "daily-arXiv-ai-enhanced 数据获取失败：" + "; ".join(errors) + "。\n"
        "建议：1) 换一个日期（--date YYYY-MM-DD，可用最近几天）；"
        "2) 改用 `lit_search --source arxiv`；3) 稍后重试（上游数据每日更新有延迟）。"
    )


def _normalize_daily_record(raw: dict[str, Any], *, date: str = "") -> tuple[PaperRecord, dict[str, Any]] | None:
    title = _clean(raw.get("title"))
    raw_id = _clean(raw.get("id"))
    if not title and not raw_id:
        return None
    arxiv_id = normalize_arxiv_id(raw_id) or raw_id
    abstract = _clean(raw.get("summary"))  # plain 文件里 summary 即英文摘要
    ai_raw = raw.get("AI") if isinstance(raw.get("AI"), dict) else {}
    ai = {str(key): _clean(value) for key, value in ai_raw.items() if _clean(value)}
    tldr = ai.get("tldr") or ""
    summary = tldr or _first_sentences(abstract, 2)
    categories = _as_list(raw.get("categories"))
    tags = ["daily"] + (["ai-enhanced"] if ai else [])
    record = PaperRecord(
        id=arxiv_id or safe_id(title),
        title=title,
        authors=_as_list(raw.get("authors")),
        abstract=abstract,
        summary=summary,
        url=_clean(raw.get("abs")) or (ARXIV_ABS.format(arxiv_id=arxiv_id) if arxiv_id else ""),
        pdf_url=_clean(raw.get("pdf")) or (ARXIV_PDF.format(arxiv_id=arxiv_id) if arxiv_id else ""),
        published=_clean(date)[:10],
        categories=categories,
        source="daily",
        tags=tags,
    )
    if raw.get("comment"):
        ai.setdefault("comment", _clean(raw.get("comment")))
    return record, ai


def parse_daily_jsonl_with_ai(
    text: str,
    *,
    date: str = "",
) -> list[tuple[PaperRecord, dict[str, Any]]]:
    """解析 daily jsonl，返回 (记录, AI 增强字段) 列表；坏行跳过。"""
    pairs: list[tuple[PaperRecord, dict[str, Any]]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        parsed = _normalize_daily_record(raw, date=date)
        if parsed is not None:
            pairs.append(parsed)
    if not pairs and str(text or "").strip():
        raise LiteratureError(
            "daily jsonl 内容无法解析（字段格式可能已变更）。请改用 --source arxiv 并反馈该问题。"
        )
    return pairs


def parse_daily_jsonl(text: str, *, date: str = "") -> list[PaperRecord]:
    """解析 daily jsonl 为 PaperRecord 列表（忽略 AI 增强细节）。"""
    return [record for record, _ in parse_daily_jsonl_with_ai(text, date=date)]


def _category_matches(categories: list[str], category: str | None) -> bool:
    wanted = str(category or "").strip()
    if not wanted:
        return True
    for item in categories:
        item = str(item).strip()
        if not item:
            continue
        if item.casefold() == wanted.casefold() or item.casefold().startswith(wanted.casefold() + "."):
            return True
    return False


def daily_arxiv_search_enhanced(
    *,
    date: str | None = None,
    category: str | None = None,
    limit: int = 20,
    language: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> tuple[str, list[tuple[PaperRecord, dict[str, Any]]]]:
    """取 daily-arXiv-ai-enhanced 的 AI 增强数据，返回 (实际日期, [(记录, ai)])。"""
    cfg = _lit_settings(settings)
    lang = language or str(getattr(cfg, "daily_arxiv_language", None) or "Chinese")
    files = fetch_daily_file_list(client=client, settings=settings)
    filename = pick_daily_file(files, date=date, language=lang)
    text, used_file = _fetch_daily_payload(filename, client=client, settings=settings)
    resolved = _file_date(used_file) or _normalize_date(date) or ""
    pairs = parse_daily_jsonl_with_ai(text, date=resolved)
    filtered = [(record, ai) for record, ai in pairs if _category_matches(record.categories, category)]
    if not filtered:
        raise LiteratureError(
            f"{resolved or used_file} 没有匹配的论文"
            + (f"（category={category}）" if category else "")
            + "。建议去掉 --category、换日期或改用 --source arxiv。"
        )
    return resolved, filtered[: max(1, min(int(limit or 20), MAX_LIMIT))]


def daily_arxiv_search(
    *,
    date: str | None = None,
    category: str | None = None,
    limit: int = 20,
    language: str | None = None,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> list[PaperRecord]:
    """按类别/日期取 daily-arXiv-ai-enhanced 的论文（含中文 tldr 写入 summary 字段）。"""
    _, pairs = daily_arxiv_search_enhanced(
        date=date,
        category=category,
        limit=limit,
        language=language,
        client=client,
        settings=settings,
    )
    return [record for record, _ in pairs]


# ---------------------------------------------------------------------------
# 标识解析 / PDF 下载
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)


def normalize_doi(value: str) -> str | None:
    """从 DOI / doi.org URL 中提取 DOI（去尾部标点）；非 DOI 返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
    match = _DOI_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;:)")


def _identifier_candidates(identifier: str) -> list[str]:
    raw = str(identifier or "").strip()
    candidates = [raw]
    arxiv_id = normalize_arxiv_id(raw)
    doi = normalize_doi(raw)
    if arxiv_id:
        candidates.extend([arxiv_id, f"arXiv:{arxiv_id}"])
    if doi:
        candidates.extend([doi, f"https://doi.org/{doi}"])
    result: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def find_library_record(library: PaperLibrary, identifier: str) -> PaperRecord | None:
    """在文献库中按 arXiv ID / DOI / 原始标识查找记录。"""
    for candidate in _identifier_candidates(identifier):
        record = library.get(candidate)
        if record is not None:
            return record
    return None


def _download_pdf(
    url: str,
    *,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> bytes:
    """下载 PDF 并校验文件头；失败抛 :class:`LiteratureError`。"""
    with open_client(client, settings=settings, timeout=max(_request_timeout(settings), 60.0)) as http:
        resp = _get(
            http,
            url,
            source="PDF 下载",
            headers={"Accept": "application/pdf,*/*"},
        )
    if resp.status_code != 200:
        raise LiteratureError(f"PDF 下载失败（HTTP {resp.status_code}）：{url}")
    content = resp.content
    if not content:
        raise LiteratureError(f"PDF 下载内容为空：{url}")
    if not content.lstrip()[:5].startswith(b"%PDF"):
        content_type = resp.headers.get("content-type", "未知")
        raise LiteratureError(
            f"下载内容不是 PDF（Content-Type: {content_type}）：{url}。"
            "可能是需要登录/验证码的落地页，请手动下载后传本地路径。"
        )
    return content


def _arxiv_metadata_record(
    ctx: ToolContext,
    arxiv_id: str,
    *,
    client: httpx.Client | None = None,
) -> PaperRecord:
    try:
        records = arxiv_get_by_ids([arxiv_id], client=client, settings=ctx.settings)
        if records:
            return records[0]
    except LiteratureError as exc:
        ctx.log(f"arXiv 元数据补全失败（不影响 PDF 下载）：{exc}", level="warn")
    return PaperRecord(
        id=arxiv_id,
        title=f"arXiv:{arxiv_id}",
        url=ARXIV_ABS.format(arxiv_id=arxiv_id),
        pdf_url=ARXIV_PDF.format(arxiv_id=arxiv_id),
        source="arxiv",
        tags=["arxiv"],
    )


def _unpaywall_pdf_url(
    doi: str,
    *,
    client: httpx.Client | None = None,
    settings: Any = None,
) -> str | None:
    email = _contact_email(settings)
    if not email:
        return None
    url = UNPAYWALL_API.format(doi=quote(doi, safe="/")) + "?email=" + quote(email)
    try:
        with open_client(client, settings=settings) as http:
            resp = _get(http, url, source="Unpaywall")
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except (LiteratureError, ValueError):
        return None
    best = payload.get("best_oa_location") or {}
    return _clean(best.get("url_for_pdf")) or None


def fetch_pdf_for_identifier(
    ctx: ToolContext,
    identifier: str,
    *,
    client: httpx.Client | None = None,
) -> tuple[bytes, PaperRecord, str]:
    """解析 arXiv ID / DOI / URL 并下载 PDF，返回 (字节, 记录, PDF URL)。

    下载顺序：arXiv 直链 → Unpaywall（需 email）→ OpenAlex OA。
    失败抛 :class:`LiteratureError`（含可操作建议）。
    """
    settings = ctx.settings
    library = PaperLibrary(ctx.data_dir)
    record = find_library_record(library, identifier)
    arxiv_id = normalize_arxiv_id(identifier)
    if arxiv_id:
        pdf_url = ARXIV_PDF.format(arxiv_id=arxiv_id)
        content = _download_pdf(pdf_url, client=client, settings=settings)
        result = record or _arxiv_metadata_record(ctx, arxiv_id, client=client)
        result.pdf_url = result.pdf_url or pdf_url
        return content, result, pdf_url

    doi = normalize_doi(identifier)
    if doi:
        result = record or _openalex_work_by_doi(doi, client=client, settings=settings)
        candidates: list[str] = []
        unpaywall_url = _unpaywall_pdf_url(doi, client=client, settings=settings)
        if unpaywall_url:
            candidates.append(unpaywall_url)
        if result is not None and result.pdf_url and result.pdf_url not in candidates:
            candidates.append(result.pdf_url)
        errors: list[str] = []
        for candidate in candidates:
            try:
                content = _download_pdf(candidate, client=client, settings=settings)
            except LiteratureError as exc:
                errors.append(str(exc))
                continue
            if result is None:
                result = PaperRecord(
                    id=doi,
                    title=doi,
                    url=f"https://doi.org/{doi}",
                    pdf_url=candidate,
                    source="openalex",
                    tags=["open-access"],
                )
            result.pdf_url = result.pdf_url or candidate
            return content, result, candidate
        raise LiteratureError(
            f"DOI {doi} 未找到可下载的开放获取 PDF。"
            + (f"\n尝试记录：{'; '.join(errors)}" if errors else "")
            + "\n建议：1) 在 ~/.sciforge/config.toml 的 [literature] 配置 email（启用 Unpaywall）；"
            "2) 手动下载 PDF 后把本地路径传给 paper_read；"
            "3) 若论文有 arXiv 版本，改用 arXiv ID 重试。"
        )

    text = str(identifier or "").strip()
    if re.match(r"^https?://", text, flags=re.IGNORECASE):
        try:
            content = _download_pdf(text, client=client, settings=settings)
        except LiteratureError as exc:
            raise LiteratureError(
                f"URL 下载 PDF 失败：{text}\n{exc}\n"
                "建议：该 URL 可能不是直接 PDF 链接；请改用 arXiv ID / DOI，"
                "或手动下载 PDF 后把本地路径传给 paper_read。"
            ) from exc
        result = record or PaperRecord(
            id=safe_id(text),
            title=Path(text).name or text,
            url=text,
            pdf_url=text,
            source="manual",
            tags=["manual"],
        )
        return content, result, text

    raise LiteratureError(
        f"无法识别的文献标识：{identifier!r}。支持：arXiv ID（2401.00001）、"
        "DOI（10.1234/abcd）、arXiv/DOI URL，或本地 PDF 路径。"
    )


def resolve_local_pdf(
    ctx: ToolContext,
    identifier: str,
    *,
    client: httpx.Client | None = None,
) -> tuple[Path | None, PaperRecord | None, list[str]]:
    """把文献标识解析成本地文件（PDF/txt/md/html）。

    依次尝试：本地路径 → 文献库已缓存 PDF → 在线下载。
    不抛异常，返回 ``(路径 | None, 记录 | None, 尝试记录)``。
    """
    attempts: list[str] = []
    library = PaperLibrary(ctx.data_dir)
    local = Path(str(identifier)).expanduser()
    if not local.is_absolute():
        local = ctx.project_dir / local
    try:
        if local.is_file() and local.suffix.lower() in LOCAL_TEXT_SUFFIXES:
            record = find_library_record(library, identifier) or library.get(local.stem)
            attempts.append(f"使用本地文件：{local}")
            return local, record, attempts
    except OSError:  # pragma: no cover - 超长路径等
        pass

    record = find_library_record(library, identifier)
    if record is not None and record.local_pdf:
        cached = Path(record.local_pdf)
        if cached.is_file():
            attempts.append(f"使用文献库缓存：{cached}")
            return cached, record, attempts
        attempts.append(f"文献库记录的 PDF 已不存在：{cached}")

    try:
        content, new_record, pdf_url = fetch_pdf_for_identifier(ctx, identifier, client=client)
    except LiteratureError as exc:
        attempts.append(str(exc))
        if shutil.which("instsci"):
            attempts.append(
                "检测到 `instsci` 命令：可尝试用 instsci 下载 PDF 后把本地路径传给 paper_read。"
            )
        else:
            attempts.append("未安装 `instsci`（可选 OA 下载器）：`pip install instsci`；或手动下载 PDF。")
        return None, record, attempts

    path = library.save_pdf(new_record.id, content)
    new_record.local_pdf = str(path)
    library.add(new_record)
    attempts.append(f"下载成功：{pdf_url} → {path}")
    return path, record or new_record, attempts


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------


def _bibtex_escape(text: str, *, escape: bool = True) -> str:
    value = _clean(text)
    if not escape:
        return value
    return re.sub(r"([&%$#_])", r"\\\1", value)


def _record_to_bibtex(record: PaperRecord) -> tuple[str, str]:
    """生成 BibTeX 条目，返回 (key, 条目文本)。"""
    first_author = record.authors[0] if record.authors else ""
    last_name = _clean(first_author).split()[-1] if first_author.split() else "paper"
    last_name = re.sub(r"[^A-Za-z]", "", last_name).lower() or "paper"
    year = record.year or "0000"
    title_words = re.findall(r"[A-Za-z]{3,}", record.title)
    title_word = title_words[0].lower() if title_words else ""
    key = f"{last_name}{year}{title_word}"
    doi = normalize_doi(record.id)
    arxiv_id = record.arxiv_id
    venue = record.venue
    if arxiv_id:
        entry_type = "article"
    elif venue:
        entry_type = "inproceedings"
    else:
        entry_type = "misc"
    fields: list[str] = []

    def add_field(name: str, value: str, *, escape: bool = True) -> None:
        value = _bibtex_escape(value, escape=escape)
        if value:
            fields.append(f"  {name} = {{{value}}},")

    add_field("title", record.title)
    add_field("author", " and ".join(record.authors))
    add_field("year", year)
    if venue:
        add_field("journal" if entry_type == "article" else "booktitle", venue)
    if arxiv_id:
        add_field("eprint", arxiv_id, escape=False)
        add_field("archivePrefix", "arXiv")
        if record.categories:
            add_field("primaryClass", record.categories[0])
        if not venue:
            add_field("journal", f"arXiv preprint arXiv:{arxiv_id}", escape=False)
    if doi:
        add_field("doi", doi, escape=False)
    if record.url:
        add_field("url", record.url, escape=False)
    if record.published:
        add_field("note", f"Published: {record.published}", escape=False)
    body = "\n".join(fields)
    return key, f"@{entry_type}{{{key},\n{body}\n}}\n"


def record_to_bibtex(record: PaperRecord) -> str:
    """单条 BibTeX（供测试/其它模块复用）。"""
    return _record_to_bibtex(record)[1]


# ---------------------------------------------------------------------------
# Tool 实现
# ---------------------------------------------------------------------------


def _resolve_sources(kwargs: dict[str, Any]) -> list[str]:
    raw: Any = kwargs.get("sources")
    if raw is None and kwargs.get("source"):
        raw = kwargs.get("source")
    if raw is None:
        raw = ["arxiv"]
    if isinstance(raw, str):
        raw = [part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()]
    sources: list[str] = []
    for item in raw or []:
        key = str(item).strip().lower()
        canonical = SOURCE_ALIASES.get(key)
        if canonical and canonical not in sources:
            sources.append(canonical)
        elif canonical is None and key:
            sources.append(f"unknown:{key}")
    return sources or ["arxiv"]


def _record_brief(record: PaperRecord, index: int) -> str:
    lines = [f"{index}. **{record.title or record.id}**"]
    meta = [f"id: `{record.id}`", f"来源: {record.source}"]
    if record.published:
        meta.append(record.published)
    if record.categories:
        meta.append(", ".join(record.categories[:3]))
    lines.append(f"   - {' | '.join(meta)}")
    if record.short_authors:
        lines.append(f"   - 作者: {record.short_authors}")
    teaser = record.summary or record.abstract
    if teaser:
        lines.append(f"   - 摘要: {_shorten(teaser, 260)}")
    link = record.best_url or record.pdf_url
    if link:
        lines.append(f"   - 链接: {link}")
    return "\n".join(lines)


class LitSearchTool(BaseTool):
    name = "lit_search"
    description = (
        "检索学术文献并写入本地文献库。支持多来源：arxiv（默认，最稳）、pubmed（医学/生命科学）、"
        "europe_pmc（含 PMC/预印本）、openreview（ICLR/NeurIPS 等会议）、openalex、hf_papers、"
        "daily（daily-arXiv AI 增强中文摘要）、crossref（可用 publisher 过滤 IEEE/ACM/Springer 等题录）。"
        "何时用：做文献调研/相关工作/找最新进展、确定创新点前需要收集论文时。"
        "多来源失败会自动降级；返回标题、作者、摘要、链接，可用 paper_read 精读。"
    )
    tags = ("literature",)
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词（英文效果最好），如 'diffusion transformer video generation'；"
                "source=daily 时可留空。",
            },
            "sources": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "arxiv",
                        "pubmed",
                        "europe_pmc",
                        "openreview",
                        "openalex",
                        "hf_papers",
                        "daily",
                        "crossref",
                    ],
                },
                "description": "来源列表，默认 [arxiv]；多来源结果交替合并。",
            },
            "limit": {"type": "integer", "description": "返回条数上限，默认 20，最大 100。"},
            "since": {
                "type": "string",
                "description": "最早时间：YYYY-MM-DD、YYYY，或相对时间 7d/30d/1y（可选）。",
            },
            "category": {
                "type": "string",
                "description": "arXiv/OpenAlex 类别过滤，如 cs.CV、cs.CL、cs.LG（可选）。",
            },
            "publisher": {
                "type": "string",
                "enum": ["ieee", "acm", "springer", "elsevier", "wiley", "taylor_francis", "jstor"],
                "description": "仅 source=crossref 时生效：按出版社过滤题录（免费，不需要订阅）。",
            },
            "save": {
                "type": "boolean",
                "description": "是否写入本地文献库，默认 true；后续 paper_read/paper_cite 可直接引用。",
            },
        },
        "required": ["query"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query") or "").strip()
        sources = _resolve_sources(kwargs)
        limit = max(1, min(int(kwargs.get("limit") or 20), MAX_LIMIT))
        since = kwargs.get("since")
        category = kwargs.get("category")
        save = bool(kwargs.get("save", True))
        client = ctx.extra.get("http_client")
        library = PaperLibrary(ctx.data_dir)

        groups: list[list[PaperRecord]] = []
        errors: list[str] = []
        for source in sources:
            ctx.log(f"检索 {source}：{query or '(daily)'}")
            try:
                if source == "arxiv":
                    if not query and not category:
                        raise LiteratureError("arXiv 检索需要 query 或 category。")
                    found = arxiv_search(
                        query,
                        limit=limit,
                        since=since,
                        category=category,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "pubmed":
                    found = pubmed_search(
                        query,
                        limit=limit,
                        since=since,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "europe_pmc":
                    found = europe_pmc_search(
                        query,
                        limit=limit,
                        since=since,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "openreview":
                    found = openreview_search(
                        query,
                        limit=limit,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "semantic_scholar":
                    found = semantic_scholar_search(
                        query,
                        limit=limit,
                        since=since,
                        category=category,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "openalex":
                    found = openalex_search(
                        query,
                        limit=limit,
                        since=since,
                        category=category,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "hf_papers":
                    found = hf_papers_search(
                        query,
                        limit=limit,
                        daily=not query,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "daily":
                    found = daily_arxiv_search(
                        category=category,
                        limit=limit,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source == "crossref":
                    found = crossref_search(
                        query,
                        publisher=kwargs.get("publisher"),
                        limit=limit,
                        since=since,
                        client=client,
                        settings=ctx.settings,
                    )
                elif source.startswith("unknown:"):
                    raise LiteratureError(
                        f"未知来源 {source.split(':', 1)[1]!r}。"
                        f"可用：{', '.join(sorted(set(SOURCE_ALIASES.values())))}"
                    )
                else:  # pragma: no cover - 防御式分支
                    raise LiteratureError(f"未实现的数据源：{source}")
            except LiteratureError as exc:
                errors.append(f"{source}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - 单个来源失败不影响其它来源
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
                continue
            groups.append(found)

        records = _interleave(groups, limit)
        saved = 0
        if save and records:
            for record in records:
                library.add(record)
                saved += 1

        if not records:
            detail = "\n".join(f"- {message}" for message in errors) or "查询未命中任何结果。"
            return ToolResult.failure(
                f"文献检索无结果。\n{detail}\n"
                "建议：1) 换更通用的英文关键词；2) 加 --category cs.CV/cs.CL 缩小范围；"
                "3) 去掉 since 时间限制；4) 改用 --source arxiv 验证网络是否通。"
            )

        lines = [f"检索完成：{len(records)} 篇（来源：{', '.join(sources)}）"]
        lines.extend(_record_brief(record, index) for index, record in enumerate(records, 1))
        if saved:
            lines.append(f"\n已写入文献库：{library.index_path}（{saved} 篇）")
        if errors:
            lines.append("\n[部分来源失败]")
            lines.extend(f"- {message}" for message in errors)
        content = "\n".join(lines)
        if len(content) > 12000:
            content = content[:12000] + "\n…（结果已截断，完整数据见 data.papers）"
        return ToolResult.success(
            content,
            data={
                "query": query,
                "sources": sources,
                "count": len(records),
                "papers": [record.to_dict() for record in records],
                "errors": errors,
                "saved": saved,
                "library": str(library.index_path),
            },
        )


class LitDailyTool(BaseTool):
    name = "lit_daily"
    description = (
        "获取 daily-arXiv-ai-enhanced 的每日 AI 增强摘要（中文 tldr/动机/方法/结果/结论），"
        "返回标题 + 一句话总结 + arXiv 链接，并写入文献库。"
        "何时用：每天追踪 cs.CV/cs.CL/cs.LG/cs.AI 新论文、快速筛选值得精读的论文。"
        "可用 category 过滤类别、date 指定日期（默认最新一天）。"
    )
    tags = ("literature",)
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "类别过滤，如 cs.CV、cs.CL、cs.LG、cs.AI（可选）。",
            },
            "date": {
                "type": "string",
                "description": "日期 YYYY-MM-DD；默认最新一天。最近一天可能尚未生成增强数据。",
            },
            "limit": {"type": "integer", "description": "返回条数上限，默认 20。"},
            "language": {
                "type": "string",
                "description": "AI 增强语言：Chinese（默认）或 English。",
            },
        },
        "required": [],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        category = kwargs.get("category")
        date = kwargs.get("date")
        limit = max(1, min(int(kwargs.get("limit") or 20), MAX_LIMIT))
        language = kwargs.get("language")
        client = ctx.extra.get("http_client")
        library = PaperLibrary(ctx.data_dir)
        try:
            resolved_date, pairs = daily_arxiv_search_enhanced(
                date=date,
                category=category,
                limit=limit,
                language=language,
                client=client,
                settings=ctx.settings,
            )
        except LiteratureError as exc:
            return ToolResult.failure(f"每日 arXiv 获取失败：{exc}")
        records = [record for record, _ in pairs]
        for record in records:
            library.add(record)
        lines = [
            f"daily-arXiv-ai-enhanced {resolved_date}：{len(records)} 篇"
            + (f"（category={category}）" if category else "")
        ]
        for index, (record, ai) in enumerate(pairs, 1):
            lines.append(f"{index}. **{record.title}**")
            meta = [f"id: `{record.id}`", f"分类: {', '.join(record.categories[:3]) or '-'}"]
            lines.append(f"   - {' | '.join(meta)}")
            if record.summary:
                lines.append(f"   - 一句话：{_shorten(record.summary, 240)}")
            if ai.get("motivation"):
                lines.append(f"   - 动机：{_shorten(ai['motivation'], 180)}")
            link = record.url or record.pdf_url
            if link:
                lines.append(f"   - 链接: {link}")
        lines.append(f"\n已写入文献库：{library.index_path}（{len(records)} 篇）")
        content = "\n".join(lines)
        if len(content) > 12000:
            content = content[:12000] + "\n…（结果已截断）"
        return ToolResult.success(
            content,
            data={
                "date": resolved_date,
                "category": category,
                "count": len(records),
                "papers": [record.to_dict() for record in records],
                "ai": {record.id: ai for record, ai in pairs},
                "library": str(library.index_path),
            },
        )


class PaperFetchTool(BaseTool):
    name = "paper_fetch"
    description = (
        "把论文 PDF 下载到本地文献库并返回本地路径。支持 arXiv ID、DOI、arXiv/DOI URL。"
        "下载顺序：arXiv 直链 → Unpaywall（需配置 [literature] email）→ OpenAlex OA，"
        "可选调用 instsci（若已安装，复用你的机构访问）。"
        "仅使用合法开放获取/机构授权来源，不访问 Sci-Hub 等未授权镜像。"
        "何时用：精读前需要全文 PDF、或 paper_read 提示本地无 PDF 时。"
        "失败会给出可操作建议（手动下载/配置 email/改用 arXiv 版本）。"
    )
    tags = ("literature", "paper")
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "arXiv ID（2401.00001）、DOI（10.1234/abcd）、arxiv.org/doi.org URL，"
                "或本地 PDF 路径。",
            },
            "use_instsci": {
                "type": "boolean",
                "description": "是否优先尝试本机 instsci 命令下载（需自行 pip install instsci），默认 false。",
            },
        },
        "required": ["paper_id"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        identifier = str(self.require(kwargs, "paper_id")).strip()
        client = ctx.extra.get("http_client")
        library = PaperLibrary(ctx.data_dir)
        attempts: list[str] = []
        if bool(kwargs.get("use_instsci")):
            instsci_path = _try_instsci_download(ctx, identifier)
            if instsci_path is not None:
                content = instsci_path.read_bytes()
                record = find_library_record(library, identifier) or PaperRecord(
                    id=normalize_arxiv_id(identifier) or safe_id(identifier),
                    title=instsci_path.stem,
                    source="manual",
                    tags=["instsci"],
                )
                target = library.save_pdf(record.id, content)
                record.local_pdf = str(target)
                library.add(record)
                return ToolResult.success(
                    f"instsci 下载成功：{target}",
                    data={
                        "identifier": identifier,
                        "paper_id": record.id,
                        "pdf_path": str(target),
                        "attempts": [f"instsci: {instsci_path}"],
                    },
                    artifacts=[str(target)],
                )
            attempts.append(
                "instsci 未找到可用下载结果（命令是否已安装/参数是否兼容未知），已回退内置下载。"
            )

        path, record, resolve_attempts = resolve_local_pdf(ctx, identifier, client=client)
        attempts.extend(resolve_attempts)
        if path is None:
            return ToolResult.failure(
                "PDF 获取失败。\n尝试记录：\n"
                + "\n".join(f"- {item}" for item in attempts)
                + "\n建议：手动下载 PDF 后把本地路径传给 paper_read，或用 arXiv ID 重试。"
            )
        size = path.stat().st_size if path.exists() else 0
        title = (record.title if record is not None else "") or path.name
        content = (
            f"PDF 已就绪：{path}\n"
            f"- 论文：{title}\n"
            f"- 标识：{record.id if record is not None else identifier}\n"
            f"- 大小：{size / 1024:.1f} KB\n"
            f"- 下一步：paper_read paper={path} 做结构化精读，或 paper_note 生成笔记。"
        )
        if len(attempts) > 1:
            content += "\n[下载过程]\n" + "\n".join(f"- {item}" for item in attempts)
        return ToolResult.success(
            content,
            data={
                "identifier": identifier,
                "paper_id": record.id if record is not None else identifier,
                "pdf_path": str(path),
                "size_bytes": size,
                "attempts": attempts,
            },
            artifacts=[str(path)],
        )


def _try_instsci_download(ctx: ToolContext, identifier: str) -> Path | None:  # pragma: no cover - 外部工具
    """可选的 instsci 集成：命令存在时 shell 调用，未知参数/未安装时返回 None。"""
    executable = shutil.which("instsci")
    if not executable:
        ctx.log("未安装 instsci，跳过 instsci 下载。", level="warn")
        return None
    target_dir = ctx.session_dir / "instsci"
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [executable, "fetch", identifier, "--output", str(target_dir)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        ctx.log(f"instsci 调用失败：{exc}", level="warn")
        return None
    if proc.returncode != 0:
        ctx.log(f"instsci 返回 {proc.returncode}：{(proc.stderr or proc.stdout)[:200]}", level="warn")
        return None
    pdfs = sorted(target_dir.glob("*.pdf"))
    return pdfs[-1] if pdfs else None


class PaperCiteTool(BaseTool):
    name = "paper_cite"
    description = (
        "为论文生成 BibTeX 引用并写入项目 references.bib（自动去重）。"
        "支持 arXiv ID / DOI / 已在文献库中的论文；arXiv 元数据通过官方 API 补全。"
        "何时用：写 related work、实验对比表、论文草稿需要引用时。返回 BibTeX 文本与文件路径。"
    )
    tags = ("literature", "writing")
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "papers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "论文标识列表：arXiv ID / DOI / 文献库 ID。",
            },
            "save": {
                "type": "boolean",
                "description": "是否写入项目 references.bib，默认 true。",
            },
            "path": {
                "type": "string",
                "description": "BibTeX 输出路径（相对项目目录或绝对路径），默认 references.bib。",
            },
        },
        "required": ["papers"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        raw = kwargs.get("papers")
        if isinstance(raw, str):
            raw = [raw]
        identifiers = [str(item).strip() for item in raw or [] if str(item).strip()]
        if not identifiers:
            return ToolResult.failure("请提供至少一篇论文标识（arXiv ID / DOI）。")
        client = ctx.extra.get("http_client")
        library = PaperLibrary(ctx.data_dir)
        entries: list[dict[str, str]] = []
        errors: list[str] = []
        used_keys: dict[str, int] = {}
        for identifier in identifiers:
            record = find_library_record(library, identifier)
            if record is None:
                record = self._lookup_record(ctx, identifier, client=client)
            if record is None:
                errors.append(identifier)
                continue
            library.add(record)
            key, bibtex = _record_to_bibtex(record)
            if key in used_keys:
                used_keys[key] += 1
                suffixed = f"{key}{chr(ord('a') + used_keys[key] - 1)}"
                bibtex = bibtex.replace(f"{{{key},", f"{{{suffixed},", 1)
                key = suffixed
            else:
                used_keys[key] = 0
            entries.append({"id": record.id, "key": key, "bibtex": bibtex, "title": record.title})

        if not entries:
            return ToolResult.failure(
                "未能获取任何论文的元数据，无法生成 BibTeX。\n"
                f"失败的标识：{', '.join(errors)}\n"
                "建议：先用 lit_search 检索并入文献库，再引用；或确认 arXiv ID/DOI 拼写。"
            )

        save = bool(kwargs.get("save", True))
        output_path: Path | None = None
        added = 0
        if save:
            output_path = Path(str(kwargs.get("path") or "references.bib")).expanduser()
            if not output_path.is_absolute():
                output_path = ctx.project_dir / output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            existing = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            chunks = [existing.rstrip()] if existing.strip() else []
            for entry in entries:
                if f"{{{entry['key']}," in existing:
                    continue
                chunks.append(entry["bibtex"].rstrip())
                added += 1
            output_path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")

        content = "\n\n".join(entry["bibtex"] for entry in entries).strip()
        header = f"已生成 {len(entries)} 条 BibTeX"
        if output_path is not None:
            header += f"（新增 {added} 条）→ {output_path}"
        if errors:
            header += f"\n[未找到元数据] {', '.join(errors)}"
        return ToolResult.success(
            f"{header}\n\n{content}",
            data={
                "entries": entries,
                "path": str(output_path) if output_path is not None else "",
                "added": added,
                "missing": errors,
            },
            artifacts=[str(output_path)] if output_path is not None and save else [],
        )

    @staticmethod
    def _lookup_record(
        ctx: ToolContext,
        identifier: str,
        *,
        client: httpx.Client | None,
    ) -> PaperRecord | None:
        arxiv_id = normalize_arxiv_id(identifier)
        if arxiv_id:
            try:
                records = arxiv_get_by_ids([arxiv_id], client=client, settings=ctx.settings)
                if records:
                    return records[0]
            except LiteratureError as exc:
                ctx.log(f"arXiv 元数据获取失败：{exc}", level="warn")
        doi = normalize_doi(identifier)
        if doi:
            try:
                record = _openalex_work_by_doi(doi, client=client, settings=ctx.settings)
                if record is not None:
                    return record
            except LiteratureError as exc:
                ctx.log(f"OpenAlex 元数据获取失败：{exc}", level="warn")
        return None


# ---------------------------------------------------------------------------
# CLI（§14.6：sciforge lit ...）
# ---------------------------------------------------------------------------


def cli_context(project_dir: str | Path | None = None) -> ToolContext:
    """构造 CLI 场景下的 ToolContext（不依赖 Lead 的 cli.py）。"""
    from sciforge.config import data_home, find_project_root, get_settings

    project = (
        Path(project_dir).expanduser().resolve()
        if project_dir
        else (find_project_root() or Path.cwd().resolve())
    )
    settings = get_settings(project)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_dir = project / ".sciforge" / "sessions" / f"cli-{timestamp}"
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - 只读目录兜底
        session_dir = project
    return ToolContext(
        settings=settings,
        project_dir=project,
        data_dir=data_home(),
        session_dir=session_dir,
    )


def _emit_cli_result(result: ToolResult, *, json_output: bool) -> None:
    if json_output:
        console.print_json(json.dumps(result.data, ensure_ascii=False, default=str))
    elif result.ok:
        console.print(result.content)
        for artifact in result.artifacts:
            console.print(f"[dim]artifact: {artifact}[/dim]")
    else:
        console.print(f"[red]{result.error or result.content}[/red]")
    if not result.ok:
        raise typer.Exit(code=1)


def add_cli(app: typer.Typer) -> None:
    """挂载 ``sciforge lit search|daily|fetch|cite|list`` 子命令。"""
    lit_app = typer.Typer(
        name="lit",
        help="文献检索：arXiv / PubMed / Europe PMC / OpenReview / OpenAlex / HF Papers / daily arXiv / Crossref",
        no_args_is_help=True,
    )
    app.add_typer(lit_app, name="lit")

    @lit_app.command("search")
    def lit_search_cmd(
        query: str = typer.Argument(..., help="检索关键词（英文效果最好）"),
        sources: list[str] = typer.Option(
            ["arxiv"], "--source", "-s", help="来源，可重复：arxiv/openalex/daily/..."
        ),
        limit: int = typer.Option(20, "--limit", "-n", help="返回条数"),
        since: str | None = typer.Option(None, "--since", help="YYYY-MM-DD 或 7d/30d/1y"),
        category: str | None = typer.Option(None, "--category", help="如 cs.CV"),
        save: bool = typer.Option(True, "--save/--no-save", help="是否写入文献库"),
        json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """检索文献并写入本地文献库。"""
        result = LitSearchTool().run(
            cli_context(),
            query=query,
            sources=sources,
            limit=limit,
            since=since,
            category=category,
            save=save,
        )
        _emit_cli_result(result, json_output=json_output)

    @lit_app.command("daily")
    def lit_daily_cmd(
        category: str | None = typer.Option(None, "--category", help="如 cs.CV"),
        date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD，默认最新"),
        limit: int = typer.Option(20, "--limit", "-n", help="返回条数"),
        language: str = typer.Option("Chinese", "--language", help="Chinese | English"),
        json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """获取 daily-arXiv-ai-enhanced 的每日 AI 增强摘要。"""
        result = LitDailyTool().run(
            cli_context(),
            category=category,
            date=date,
            limit=limit,
            language=language,
        )
        _emit_cli_result(result, json_output=json_output)

    @lit_app.command("fetch")
    def lit_fetch_cmd(
        paper_id: str = typer.Argument(..., help="arXiv ID / DOI / URL / 本地 PDF"),
        instsci: bool = typer.Option(False, "--instsci", help="优先尝试 instsci 下载"),
        json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """下载论文 PDF 到本地文献库。"""
        result = PaperFetchTool().run(
            cli_context(),
            paper_id=paper_id,
            use_instsci=instsci,
        )
        _emit_cli_result(result, json_output=json_output)

    @lit_app.command("cite")
    def lit_cite_cmd(
        papers: list[str] = typer.Argument(..., help="arXiv ID / DOI，可多个"),
        output: str | None = typer.Option(None, "--output", "-o", help="输出路径，默认 references.bib"),
        no_save: bool = typer.Option(False, "--no-save", help="只打印，不写文件"),
        json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """生成 BibTeX 引用并写入 references.bib。"""
        result = PaperCiteTool().run(
            cli_context(),
            papers=list(papers),
            save=not no_save,
            path=output,
        )
        _emit_cli_result(result, json_output=json_output)

    @lit_app.command("list")
    def lit_list_cmd(
        tag: str | None = typer.Option(None, "--tag", help="按标签过滤"),
        limit: int = typer.Option(50, "--limit", "-n", help="返回条数"),
        json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """列出本地文献库中的论文。"""
        ctx = cli_context()
        library = PaperLibrary(ctx.data_dir)
        records = library.list(limit=limit, tag=tag)
        if json_output:
            console.print_json(
                json.dumps(
                    {"count": len(records), "papers": [record.to_dict() for record in records]},
                    ensure_ascii=False,
                )
            )
            return
        if not records:
            console.print("[yellow]文献库为空：先运行 `sciforge lit search <query>`。[/yellow]")
            return
        console.print(f"文献库 {library.index_path}（{len(records)} 篇）")
        for index, record in enumerate(records, 1):
            meta = " | ".join(
                part
                for part in [
                    record.id,
                    record.published[:10] if record.published else "",
                    record.source,
                ]
                if part
            )
            console.print(f"{index}. {record.title}")
            console.print(f"   [dim]{meta}[/dim]")


register_tool(LitSearchTool())
register_tool(LitDailyTool())
register_tool(PaperFetchTool())
register_tool(PaperCiteTool())


__all__ = [
    "LiteratureError",
    "ARXIV_API",
    "DAILY_FILE_LIST_URL",
    "SOURCE_ALIASES",
    "add_cli",
    "arxiv_get_by_ids",
    "arxiv_search",
    "build_arxiv_query",
    "cli_context",
    "crossref_search",
    "daily_arxiv_search",
    "daily_arxiv_search_enhanced",
    "fetch_daily_file_list",
    "fetch_pdf_for_identifier",
    "find_library_record",
    "hf_papers_search",
    "normalize_arxiv_id",
    "normalize_doi",
    "open_client",
    "openalex_search",
    "parse_arxiv_atom",
    "parse_daily_jsonl",
    "parse_daily_jsonl_with_ai",
    "pick_daily_file",
    "record_to_bibtex",
    "resolve_local_pdf",
    "semantic_scholar_get",
    "semantic_scholar_search",
    "LitDailyTool",
    "LitSearchTool",
    "PaperCiteTool",
    "PaperFetchTool",
]
