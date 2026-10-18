"""T1 文献检索 + 论文精读模块测试（完全离线：httpx.MockTransport / monkeypatch / 临时目录）。

覆盖：arXiv XML 解析与请求参数、Semantic Scholar 429 降级、OpenAlex/HF/Crossref 解析、
daily-arXiv-ai-enhanced 增强/plain 回退、PaperLibrary 落库、PDF 提取（pymupdf + pypdf 兜底）、
章节切分、精读笔记（离线 + 注入 Mock router）、paper_ask 检索、paper_compare 表格、BibTeX、CLI 注册。

真实网络测试标 ``@pytest.mark.integration`` 且默认跳过；设置 ``SCIFORGE_RUN_INTEGRATION=1`` 启用。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import typer
from typer.testing import CliRunner

from sciforge.config import Settings
from sciforge.core.registry import ToolContext, get_tool
from sciforge.library import PaperLibrary, PaperRecord
from sciforge.llm.mock import MockProvider
from sciforge.tools import literature, paper_reader

# ---------------------------------------------------------------------------
# 固定响应数据
# ---------------------------------------------------------------------------

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>Diffusion   Transformers for Video Generation</title>
    <published>2024-01-02T00:00:00Z</published>
    <summary>A diffusion transformer for video generation. It improves FVD on UCF101.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Chen</name></author>
    <link href="https://arxiv.org/abs/2401.00001v2" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2401.00001v2" rel="related" type="application/pdf" title="pdf"/>
    <arxiv:primary_category term="cs.CV"/>
    <atom:category xmlns:atom="http://www.w3.org/2005/Atom" term="cs.LG"/>
    <arxiv:journal_ref>CVPR 2024</arxiv:journal_ref>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2402.00002</id>
    <title>Sparse Attention for Long Video</title>
    <published>2024-02-03T00:00:00Z</published>
    <summary>We study sparse attention for long video modeling.</summary>
    <author><name>Carol Wu</name></author>
    <link href="https://arxiv.org/abs/2402.00002" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2402.00002" rel="application/pdf" title="pdf"/>
    <arxiv:primary_category term="cs.CV"/>
  </entry>
</feed>
"""

DAILY_FILE_LIST = "\n".join(
    [
        "2026-09-06.jsonl",
        "2026-09-06_AI_enhanced_Chinese.jsonl",
        "2026-09-07.jsonl",
        "2026-09-07_AI_enhanced_Chinese.jsonl",
    ]
)

DAILY_ENHANCED = "\n".join(
    json.dumps(record, ensure_ascii=False)
    for record in [
        {
            "id": "2409.00001",
            "categories": ["cs.CV"],
            "pdf": "https://arxiv.org/pdf/2409.00001",
            "abs": "https://arxiv.org/abs/2409.00001",
            "authors": ["Eve Lin", "Frank Xu"],
            "title": "Efficient Video Diffusion",
            "comment": "10 pages",
            "summary": "We propose an efficient video diffusion model.",
            "AI": {
                "tldr": "本文提出高效视频扩散模型，在 UCF101 上显著降低推理成本。",
                "motivation": "视频扩散推理成本高。",
                "method": "采用时空分离注意力。",
                "result": "FVD 下降 12.5%。",
                "conclusion": "方法有效但训练成本仍高。",
            },
        },
        {
            "id": "2409.00002",
            "categories": ["cs.CL"],
            "pdf": "https://arxiv.org/pdf/2409.00002",
            "abs": "https://arxiv.org/abs/2409.00002",
            "authors": "Grace Ma, Henry Li",
            "title": "Long-Context Language Models",
            "comment": "",
            "summary": "We study long-context language models.",
            "AI": {"tldr": "本文研究长上下文语言模型的位置编码。"},
        },
    ]
)

DAILY_PLAIN = "\n".join(
    json.dumps(record, ensure_ascii=False)
    for record in [
        {
            "id": "2409.00001",
            "categories": ["cs.CV"],
            "pdf": "https://arxiv.org/pdf/2409.00001",
            "abs": "https://arxiv.org/abs/2409.00001",
            "authors": ["Eve Lin"],
            "title": "Efficient Video Diffusion",
            "comment": "",
            "summary": "We propose an efficient video diffusion model. It cuts cost by half.",
        }
    ]
)

PAPER_TEXT = """Diffusion Transformers for Video Generation
Alice Smith, Bob Chen

Abstract
We propose a diffusion transformer for video generation. Our method improves FVD by 12.5% on UCF101.

1 Introduction
Video generation is challenging due to temporal consistency.

2 Related Work
Prior work uses GANs and autoregressive models.

3 Method
We propose a spatiotemporal attention module with 3D RoPE and a flow matching objective.

4 Experiments
We evaluate on UCF101 and Kinetics with FVD and PSNR. Accuracy improves by 12.5%.

5 Conclusion
Our method is effective. However, the training cost is high and future work will reduce it.

References
[1] Some paper. [2] Another paper.
"""

PAPER_TEXT_2 = """Autoregressive Video Models
Carol Wu

Abstract
We study autoregressive video models. Our method improves PSNR on Kinetics by 3%.

1 Introduction
Autoregressive models are simple.

3 Method
We propose a causal transformer trained with next-frame prediction.

4 Experiments
We evaluate on Kinetics and UCF101 with PSNR and SSIM.

5 Conclusion
Our method is simple but suffers from error accumulation.
"""


# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------


def make_ctx(tmp_path: Path, *, client: httpx.Client | None = None) -> ToolContext:
    project = tmp_path / "project"
    session = project / ".sciforge" / "sessions" / "pytest"
    session.mkdir(parents=True, exist_ok=True)
    extra: dict[str, object] = {"http_client": client} if client is not None else {}
    return ToolContext(
        settings=Settings(),
        project_dir=project,
        data_dir=tmp_path / "data",
        session_dir=session,
        extra=extra,
    )


@contextmanager
def mock_http(handler: Callable[[httpx.Request], httpx.Response]) -> Iterator[httpx.Client]:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


def json_response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def text_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, headers={"content-type": "text/plain"})


def pdf_response() -> httpx.Response:
    return httpx.Response(
        200,
        content=b"%PDF-1.4\n% fake pdf payload for tests\n",
        headers={"content-type": "application/pdf"},
    )


def write_pdf(path: Path, text: str) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 740), text, fontsize=9)
    document.save(str(path))
    document.close()
    return path


def write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 注册与 schema
# ---------------------------------------------------------------------------


def test_t1_tools_registered_with_schema() -> None:
    expectations = {
        "lit_search": "literature",
        "lit_daily": "literature",
        "paper_fetch": "literature",
        "paper_cite": "writing",
        "paper_read": "paper",
        "paper_note": "paper",
        "paper_ask": "paper",
        "paper_compare": "paper",
    }
    for name, tag in expectations.items():
        tool = get_tool(name)
        assert tool is not None, f"工具未注册: {name}"
        assert tag in tool.tags, f"{name} 缺少 tag {tag}"
        assert tool.parameters.get("type") == "object"
        assert len(tool.description) > 40
        assert "何时用" in tool.description or "用途" in tool.description
        schema = tool.schema()
        assert schema["function"]["name"] == name


def test_cli_registers_lit_and_read() -> None:
    root = typer.Typer()
    literature.add_cli(root)
    paper_reader.add_cli(root)
    runner = CliRunner()
    for args in (
        ["lit", "--help"],
        ["lit", "search", "--help"],
        ["lit", "daily", "--help"],
        ["lit", "cite", "--help"],
        ["read", "--help"],
    ):
        result = runner.invoke(root, args)
        assert result.exit_code == 0, f"{args} 失败：{result.output}"


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------


def test_build_arxiv_query_free_text() -> None:
    query = literature.build_arxiv_query("diffusion transformer", category="cs.CV")
    assert query == "all:diffusion AND all:transformer AND cat:cs.CV"


def test_build_arxiv_query_keeps_explicit_fields() -> None:
    assert literature.build_arxiv_query("ti:diffusion AND au:hinton") == "ti:diffusion AND au:hinton"
    quoted = literature.build_arxiv_query('"diffusion transformer"')
    assert quoted == 'all:"diffusion transformer"'


def test_build_arxiv_query_since_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    query = literature.build_arxiv_query("video", since="2024-01-02")
    assert "submittedDate:[202401020000 TO 209912312359]" in query
    query_year = literature.build_arxiv_query("video", since="2023")
    assert "submittedDate:[202301010000 TO 209912312359]" in query_year
    query_relative = literature.build_arxiv_query("video", since="7d")
    assert "submittedDate:[" in query_relative
    assert literature.build_arxiv_query("video", since="not-a-date") == "all:video"


def test_parse_arxiv_atom_fields() -> None:
    records = literature.parse_arxiv_atom(ARXIV_XML)
    assert len(records) == 2
    first = records[0]
    assert first.id == "2401.00001"  # 版本号已去掉
    assert first.title == "Diffusion Transformers for Video Generation"
    assert first.authors == ["Alice Smith", "Bob Chen"]
    assert first.published == "2024-01-02"
    assert first.pdf_url == "https://arxiv.org/pdf/2401.00001v2"
    assert "cs.CV" in first.categories and "cs.LG" in first.categories
    assert first.venue == "CVPR 2024"
    assert first.source == "arxiv"
    assert "arxiv" in first.tags
    assert records[1].authors == ["Carol Wu"]


def test_parse_arxiv_atom_rejects_invalid_xml() -> None:
    with pytest.raises(literature.LiteratureError, match="XML"):
        literature.parse_arxiv_atom("not xml")
    with pytest.raises(literature.LiteratureError):
        literature.parse_arxiv_atom("")


def test_arxiv_search_request_params_and_parse() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return text_response(ARXIV_XML)

    with mock_http(handler) as client:
        records = literature.arxiv_search(
            "diffusion transformer", limit=7, category="cs.CV", client=client
        )
    assert seen["search_query"] == "all:diffusion AND all:transformer AND cat:cs.CV"
    assert seen["max_results"] == "7"
    assert seen["sortBy"] == "submittedDate"
    assert seen["sortOrder"] == "descending"
    assert len(records) == 2


def test_arxiv_search_network_error_is_actionable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mock network down", request=request)

    with mock_http(handler) as client:
        with pytest.raises(literature.LiteratureError, match="网络请求失败"):
            literature.arxiv_search("video", client=client)


def test_arxiv_get_by_ids_uses_id_list() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return text_response(ARXIV_XML)

    with mock_http(handler) as client:
        records = literature.arxiv_get_by_ids(["2401.00001v2", "2402.00002"], client=client)
    assert seen["id_list"] == "2401.00001,2402.00002"
    assert len(records) == 2


# ---------------------------------------------------------------------------
# lit_search 工具
# ---------------------------------------------------------------------------


def test_lit_search_tool_writes_library(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "export.arxiv.org" in str(request.url)
        return text_response(ARXIV_XML)

    with mock_http(handler) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.LitSearchTool().run(
            ctx, query="diffusion transformer", sources=["arxiv"], limit=5
        )
    assert result.ok
    assert result.data["count"] == 2
    assert result.data["saved"] == 2
    assert result.data["errors"] == []
    assert "检索完成" in result.content
    assert "Diffusion Transformers for Video Generation" in result.content
    library = PaperLibrary(ctx.data_dir)
    assert library.get("2401.00001") is not None
    assert library.get("2401.00001").source == "arxiv"


def test_lit_search_tool_save_false(tmp_path: Path) -> None:
    with mock_http(lambda request: text_response(ARXIV_XML)) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.LitSearchTool().run(
            ctx, query="video", sources=["arxiv"], save=False
        )
    assert result.ok
    assert result.data["saved"] == 0
    assert PaperLibrary(ctx.data_dir).count() == 0


def test_lit_search_tool_unknown_source_partial_failure(tmp_path: Path) -> None:
    with mock_http(lambda request: text_response(ARXIV_XML)) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.LitSearchTool().run(
            ctx, query="video", sources=["arxiv", "bogus"]
        )
    assert result.ok
    assert result.data["count"] == 2
    assert any("bogus" in message for message in result.data["errors"])


def test_lit_search_tool_all_fail_returns_actionable_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mock down", request=request)

    with mock_http(handler) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.LitSearchTool().run(ctx, query="video", sources=["arxiv"])
    assert not result.ok
    assert "建议" in result.error
    assert "检索无结果" in result.error


# ---------------------------------------------------------------------------
# Semantic Scholar / OpenAlex / HF / Crossref
# ---------------------------------------------------------------------------


def test_semantic_scholar_429_mentions_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return text_response('{"message": "Too Many Requests"}', status=429)

    with mock_http(handler) as client:
        with pytest.raises(literature.LiteratureError, match="S2_API_KEY"):
            literature.semantic_scholar_search("video", client=client)


def test_semantic_scholar_parses_records() -> None:
    payload = {
        "data": [
            {
                "paperId": "abc123",
                "title": "S2 Paper",
                "abstract": "Abstract from S2.",
                "url": "https://www.semanticscholar.org/paper/abc123",
                "venue": "NeurIPS",
                "publicationDate": "2024-06-01",
                "authors": [{"name": "Xavier Yu"}, {"name": "Yara Zeng"}],
                "externalIds": {"ArXiv": "2402.00002", "DOI": "10.1234/s2"},
                "openAccessPdf": {"url": "https://example.org/s2.pdf"},
            }
        ]
    }

    with mock_http(lambda request: json_response(payload)) as client:
        records = literature.semantic_scholar_search("video", client=client)
    assert len(records) == 1
    record = records[0]
    assert record.id == "2402.00002"
    assert record.authors == ["Xavier Yu", "Yara Zeng"]
    assert record.pdf_url == "https://example.org/s2.pdf"
    assert record.venue == "NeurIPS"
    assert record.source == "s2"


def test_openalex_parses_inverted_abstract_and_oa_pdf() -> None:
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1234/openalex",
                "display_name": "OpenAlex Paper",
                "publication_year": 2024,
                "publication_date": "2024-03-01",
                "authorships": [
                    {"author": {"display_name": "Alice Smith"}},
                    {"author": {"display_name": "Bob Chen"}},
                ],
                "abstract_inverted_index": {
                    "We": [0],
                    "propose": [1],
                    "a": [2],
                    "method": [3],
                    ".": [4],
                },
                "primary_location": {
                    "landing_page_url": "https://example.org/work",
                    "source": {"display_name": "Nature"},
                },
                "best_oa_location": {"pdf_url": "https://example.org/work.pdf"},
                "concepts": [{"display_name": "Computer science"}],
            }
        ]
    }

    with mock_http(lambda request: json_response(payload)) as client:
        records = literature.openalex_search("method", client=client)
    assert len(records) == 1
    record = records[0]
    assert record.id == "10.1234/openalex"
    assert record.abstract == "We propose a method ."
    assert record.pdf_url == "https://example.org/work.pdf"
    assert record.venue == "Nature"
    assert record.published == "2024-03-01"


def test_hf_papers_parses_search_and_daily_shapes() -> None:
    search_payload = [
        {
            "id": "2403.00003",
            "title": "HF Paper",
            "summary": "HF abstract.",
            "authors": [{"name": "Cathy Dong"}],
            "publishedAt": "2024-03-02T00:00:00.000Z",
        }
    ]
    with mock_http(lambda request: json_response(search_payload)) as client:
        records = literature.hf_papers_search("video", client=client)
    assert records[0].id == "2403.00003"
    assert records[0].authors == ["Cathy Dong"]
    assert records[0].published == "2024-03-02"
    assert records[0].source == "hf"

    daily_payload = [{"paper": search_payload[0], "publishedAt": "2024-03-02"}]
    with mock_http(lambda request: json_response(daily_payload)) as client:
        records = literature.hf_papers_search("", daily=True, client=client)
    assert records[0].id == "2403.00003"


def test_crossref_parses_record() -> None:
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/crossref",
                    "title": ["Crossref Paper"],
                    "author": [
                        {"given": "Mia", "family": "Hou"},
                        {"given": "Ned", "family": "Tan"},
                    ],
                    "issued": {"date-parts": [[2022, 7, 1]]},
                    "container-title": ["Journal of Tests"],
                    "URL": "https://doi.org/10.1234/crossref",
                    "abstract": "<jats:p>An abstract from Crossref.</jats:p>",
                    "link": [{"content-type": "application/pdf", "URL": "https://example.org/cr.pdf"}],
                }
            ]
        }
    }
    with mock_http(lambda request: json_response(payload)) as client:
        records = literature.crossref_search("test", client=client)
    assert records[0].id == "10.1234/crossref"
    assert records[0].authors == ["Mia Hou", "Ned Tan"]
    assert records[0].venue == "Journal of Tests"
    assert records[0].published == "2022-07-01"
    assert records[0].abstract == "An abstract from Crossref."
    assert records[0].pdf_url == "https://example.org/cr.pdf"


# ---------------------------------------------------------------------------
# daily-arXiv-ai-enhanced
# ---------------------------------------------------------------------------


def test_pick_daily_file_prefers_latest_enhanced() -> None:
    files = DAILY_FILE_LIST.splitlines()
    assert (
        literature.pick_daily_file(files) == "2026-09-07_AI_enhanced_Chinese.jsonl"
    )
    assert (
        literature.pick_daily_file(files, language="English")
        == "2026-09-07_AI_enhanced_Chinese.jsonl"
    )
    assert (
        literature.pick_daily_file(files, date="2026-09-06")
        == "2026-09-06_AI_enhanced_Chinese.jsonl"
    )
    # 日期不在清单里时仍按约定构造文件名（随后可能 404 并给出建议）
    assert (
        literature.pick_daily_file(files, date="2026-09-05")
        == "2026-09-05_AI_enhanced_Chinese.jsonl"
    )


def test_parse_daily_jsonl_with_ai_fields() -> None:
    pairs = literature.parse_daily_jsonl_with_ai(DAILY_ENHANCED, date="2026-09-07")
    assert len(pairs) == 2
    record, ai = pairs[0]
    assert record.id == "2409.00001"
    assert record.summary.startswith("本文提出高效视频扩散模型")
    assert record.published == "2026-09-07"
    assert "cs.CV" in record.categories
    assert record.source == "daily"
    assert "ai-enhanced" in record.tags
    assert ai["tldr"].startswith("本文提出")
    assert ai["method"]
    # 字符串 authors 也能解析
    second, second_ai = pairs[1]
    assert second.authors == ["Grace Ma", "Henry Li"]
    assert second_ai == {"tldr": second.summary}


def test_parse_daily_plain_jsonl_fallback_summary() -> None:
    records = literature.parse_daily_jsonl(DAILY_PLAIN, date="2026-09-07")
    assert len(records) == 1
    assert records[0].summary.startswith("We propose an efficient video diffusion model.")
    assert records[0].abstract.startswith("We propose")
    assert "ai-enhanced" not in records[0].tags


def test_daily_arxiv_search_with_mock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("file-list.txt"):
            return text_response(DAILY_FILE_LIST)
        assert "2026-09-07_AI_enhanced_Chinese.jsonl" in url
        return text_response(DAILY_ENHANCED)

    with mock_http(handler) as client:
        records = literature.daily_arxiv_search(client=client, limit=10)
    assert len(records) == 2
    assert records[0].source == "daily"
    assert records[0].summary.startswith("本文提出")


def test_daily_empty_enhanced_falls_back_to_plain() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("file-list.txt"):
            return text_response(DAILY_FILE_LIST)
        if "2026-09-07_AI_enhanced_Chinese.jsonl" in url:
            return text_response("")
        if "2026-09-07.jsonl" in url:
            return text_response(DAILY_PLAIN)
        return text_response("not found", status=404)

    with mock_http(handler) as client:
        records = literature.daily_arxiv_search(client=client)
    assert len(records) == 1
    assert records[0].summary.startswith("We propose")
    assert any("_AI_enhanced_Chinese.jsonl" in url for url in requested)
    assert any(url.endswith("2026-09-07.jsonl") for url in requested)


def test_daily_both_empty_raises_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("file-list.txt"):
            return text_response(DAILY_FILE_LIST)
        return text_response("")

    with mock_http(handler) as client:
        with pytest.raises(literature.LiteratureError, match="数据获取失败"):
            literature.daily_arxiv_search(client=client)


def test_lit_daily_tool_writes_library(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("file-list.txt"):
            return text_response(DAILY_FILE_LIST)
        return text_response(DAILY_ENHANCED)

    with mock_http(handler) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.LitDailyTool().run(ctx, limit=10)
    assert result.ok
    assert result.data["date"] == "2026-09-07"
    assert result.data["count"] == 2
    assert "2409.00001" in result.data["ai"]
    assert "本文提出高效视频扩散模型" in result.content
    library = PaperLibrary(ctx.data_dir)
    assert library.get("2409.00001") is not None
    assert library.get("2409.00001").summary.startswith("本文提出")


def test_lit_daily_tool_category_filter(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("file-list.txt"):
            return text_response(DAILY_FILE_LIST)
        return text_response(DAILY_ENHANCED)

    with mock_http(handler) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.LitDailyTool().run(ctx, category="cs.CL", limit=10)
    assert result.ok
    assert result.data["count"] == 1
    assert result.data["papers"][0]["id"] == "2409.00002"


# ---------------------------------------------------------------------------
# PDF 下载 / 引用
# ---------------------------------------------------------------------------


def test_paper_fetch_arxiv_downloads_to_library(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "export.arxiv.org" in url:
            return text_response(ARXIV_XML)
        if "arxiv.org/pdf/2401.00001" in url:
            return pdf_response()
        return text_response("not found", status=404)

    with mock_http(handler) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.PaperFetchTool().run(ctx, paper_id="2401.00001")
    assert result.ok
    pdf_path = Path(result.data["pdf_path"])
    assert pdf_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert result.data["size_bytes"] > 0
    library = PaperLibrary(ctx.data_dir)
    record = library.get("2401.00001")
    assert record is not None
    assert record.local_pdf == str(pdf_path)
    assert str(pdf_path) in result.artifacts


def test_paper_fetch_failure_is_actionable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mock down", request=request)

    with mock_http(handler) as client:
        ctx = make_ctx(tmp_path, client=client)
        result = literature.PaperFetchTool().run(ctx, paper_id="2401.00001")
    assert not result.ok
    assert "PDF 获取失败" in result.error
    assert "建议" in result.error


def test_resolve_local_pdf_uses_local_file(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    local = write_text(ctx.project_dir / "paper.txt", PAPER_TEXT)
    path, record, attempts = literature.resolve_local_pdf(ctx, str(local))
    assert path == local
    assert attempts and "本地文件" in attempts[0]


def test_paper_cite_writes_bibtex_and_dedupes(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return text_response(ARXIV_XML)

    with mock_http(handler) as client:
        ctx = make_ctx(tmp_path, client=client)
        first = literature.PaperCiteTool().run(ctx, papers=["2401.00001"])
        second = literature.PaperCiteTool().run(ctx, papers=["2401.00001"])
    assert first.ok
    assert first.data["added"] == 1
    assert first.content.count("@article") >= 1
    refs_path = ctx.project_dir / "references.bib"
    assert refs_path.is_file()
    content = refs_path.read_text(encoding="utf-8")
    assert content.count("@article{") == 1
    assert "Diffusion Transformers for Video Generation" in content
    assert str(refs_path) in first.artifacts
    assert second.ok
    assert second.data["added"] == 0  # 已存在，自动去重


def test_paper_cite_without_metadata_fails(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = literature.PaperCiteTool().run(ctx, papers=["not-a-real-id"])
    assert not result.ok
    assert "未能获取" in result.error


def test_record_to_bibtex_arxiv_and_doi() -> None:
    arxiv_record = PaperRecord(
        id="2401.00001",
        title="Diffusion Transformers",
        authors=["Alice Smith"],
        published="2024-01-02",
        categories=["cs.CV"],
    )
    bibtex = literature.record_to_bibtex(arxiv_record)
    assert bibtex.startswith("@article{alice2024diffusion,")
    assert "eprint = {2401.00001}" in bibtex
    assert "archivePrefix = {arXiv}" in bibtex
    assert "primaryClass = {cs.CV}" in bibtex

    doi_record = PaperRecord(
        id="10.1234/abc",
        title="DOI Paper",
        authors=["Bob Lee"],
        published="2023-05-01",
        venue="ICML",
    )
    bibtex = literature.record_to_bibtex(doi_record)
    assert bibtex.startswith("@inproceedings{lee2023doi,")
    assert "doi = {10.1234/abc}" in bibtex
    assert "booktitle = {ICML}" in bibtex


def test_find_library_record_variants(tmp_path: Path) -> None:
    library = PaperLibrary(tmp_path / "data")
    library.add(PaperRecord(id="10.1234/abc", title="DOI Paper"))
    assert literature.find_library_record(library, "https://doi.org/10.1234/abc") is not None
    assert literature.find_library_record(library, "arXiv:2401.00001") is None


# ---------------------------------------------------------------------------
# 章节切分 / 文本提取
# ---------------------------------------------------------------------------


def test_split_sections_english_numbered() -> None:
    sections = paper_reader.split_sections(PAPER_TEXT)
    assert "preamble" in sections
    for key in ("abstract", "intro", "related", "method", "experiments", "conclusion", "references"):
        assert key in sections, f"缺少章节 {key}: {list(sections)}"
    assert "diffusion transformer" in sections["abstract"]
    assert "spatiotemporal attention" in sections["method"]
    assert "UCF101" in sections["experiments"]


def test_split_sections_chinese() -> None:
    text = """视频扩散模型
张三

摘要
本文提出一种视频扩散模型。

1 引言
视频生成很难。

3 方法
本文提出时空注意力机制。

4 实验
在 UCF101 上验证。

5 结论
方法有效。

参考文献
[1] 某篇论文。
"""
    sections = paper_reader.split_sections(text)
    for key in ("abstract", "intro", "method", "experiments", "conclusion", "references"):
        assert key in sections, f"缺少章节 {key}: {list(sections)}"


def test_split_sections_falls_back_to_body() -> None:
    sections = paper_reader.split_sections("just some text\nwithout any headings")
    assert list(sections) == ["body"]
    assert "just some text" in sections["body"]


def test_extract_title_prefers_metadata_then_first_line() -> None:
    assert paper_reader.extract_title("ignored", metadata_title="Metadata Title") == "Metadata Title"
    assert (
        paper_reader.extract_title(PAPER_TEXT)
        == "Diffusion Transformers for Video Generation"
    )
    assert paper_reader.extract_title("no title here") == ""


def test_extract_text_from_pdf_pymupdf(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "paper.pdf", PAPER_TEXT)
    text, extractor, _title, pages = paper_reader.extract_text_from_pdf(pdf)
    assert extractor == "pymupdf"
    assert pages >= 1
    assert "Diffusion Transformers" in text
    assert "UCF101" in text


def test_extract_text_from_pdf_pypdf_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = write_pdf(tmp_path / "paper.pdf", PAPER_TEXT)
    monkeypatch.setattr(paper_reader, "_extract_with_pymupdf", lambda path: None)
    text, extractor, _title, pages = paper_reader.extract_text_from_pdf(pdf)
    assert extractor == "pypdf"
    assert pages >= 1
    assert "UCF101" in text


def test_extract_text_from_pdf_failure_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = write_pdf(tmp_path / "paper.pdf", PAPER_TEXT)

    def boom(path: Path) -> tuple[str, str, int] | None:
        raise paper_reader.PaperReadError("mock extractor failure")

    monkeypatch.setattr(paper_reader, "_extract_with_pymupdf", boom)
    monkeypatch.setattr(paper_reader, "_extract_with_pypdf", boom)
    with pytest.raises(paper_reader.PaperReadError, match="OCR"):
        paper_reader.extract_text_from_pdf(pdf)


def test_chunk_text_and_retrieve() -> None:
    chunks = paper_reader.chunk_text("attention block. " * 200, size=400, overlap=50)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)
    retrieved = paper_reader.retrieve_chunks(
        ["we use 8 A100 GPUs with batch size 256", "unrelated sentence about cooking"],
        "how many GPUs and what batch size",
    )
    assert retrieved
    assert "batch size 256" in retrieved[0][1]


# ---------------------------------------------------------------------------
# paper_read / paper_note / paper_ask / paper_compare（离线）
# ---------------------------------------------------------------------------


def test_paper_read_pdf_tool(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    pdf = write_pdf(ctx.project_dir / "paper.pdf", PAPER_TEXT)
    result = paper_reader.PaperReadTool().run(ctx, paper=str(pdf))
    assert result.ok
    assert result.data["extractor"] == "pymupdf"
    assert result.data["pages"] >= 1
    assert result.data["title"] == "Diffusion Transformers for Video Generation"
    for key in ("abstract", "method", "experiments", "conclusion"):
        assert key in result.data["sections"]
        assert result.data["sections"][key]["chars"] > 0
    assert "章节结构" in result.content
    assert "下一步" in result.content
    library = PaperLibrary(ctx.data_dir)
    assert library.get(result.data["paper_id"]) is not None


def test_paper_read_plain_text_tool(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    text_path = write_text(ctx.project_dir / "paper.txt", PAPER_TEXT)
    result = paper_reader.PaperReadTool().run(ctx, paper=str(text_path))
    assert result.ok
    assert result.data["extractor"].startswith("plain")
    assert "abstract" in result.data["sections"]
    assert "method" in result.data["sections"]


def test_paper_read_missing_paper_fails(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = paper_reader.PaperReadTool().run(ctx, paper="does-not-exist.pdf")
    assert not result.ok
    assert "论文读取失败" in result.error


def test_paper_note_offline_writes_project_and_library(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    text_path = write_text(ctx.project_dir / "paper.txt", PAPER_TEXT)
    result = paper_reader.PaperNoteTool().run(ctx, paper=str(text_path), llm=False)
    assert result.ok
    assert result.data["llm_used"] is False
    note_path = Path(result.data["note_path"])
    assert note_path.is_file()
    assert note_path.parent == ctx.project_dir / "notes"
    markdown = result.data["markdown"]
    for section in (
        "## 一句话总结",
        "## 1. 研究问题与动机",
        "## 2. 方法核心",
        "## 3. 实验与结果",
        "## 4. 批判性分析",
        "## 5. 可迁移点",
    ):
        assert section in markdown, f"笔记缺少章节：{section}"
    assert "UCF101" in markdown and "FVD" in markdown
    library = PaperLibrary(ctx.data_dir)
    record = library.get(result.data["paper_id"])
    assert record is not None
    assert record.notes_path == str(note_path)
    assert str(note_path) in result.artifacts


def test_paper_note_uses_injected_mock_router(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.extra["router"] = MockProvider(scripts=["## 一句话总结\n\nLLM 精读笔记内容。"])
    text_path = write_text(ctx.project_dir / "paper.txt", PAPER_TEXT)
    result = paper_reader.PaperNoteTool().run(ctx, paper=str(text_path), llm=True)
    assert result.ok
    assert result.data["llm_used"] is True
    assert "LLM 精读笔记内容。" in result.data["markdown"]


def test_paper_ask_offline_returns_snippets(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    text_path = write_text(ctx.project_dir / "paper.txt", PAPER_TEXT)
    result = paper_reader.PaperAskTool().run(
        ctx,
        paper=str(text_path),
        question="what dataset and metric are used?",
        llm=False,
    )
    assert result.ok
    assert result.data["mode"] == "offline-retrieval"
    assert result.data["answer"] is None
    assert result.data["snippets"]
    assert "离线模式" in result.content


def test_paper_ask_uses_mock_router(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.extra["router"] = MockProvider(scripts=["根据片段，在 UCF101 上 FVD 提升 12.5%。"])
    text_path = write_text(ctx.project_dir / "paper.txt", PAPER_TEXT)
    result = paper_reader.PaperAskTool().run(
        ctx,
        paper=str(text_path),
        question="UCF101 上指标提升多少？",
        llm=True,
    )
    assert result.ok
    assert result.data["mode"] == "llm"
    assert "UCF101" in result.data["answer"]


def test_paper_compare_table_and_file(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    first = write_text(ctx.project_dir / "paper1.txt", PAPER_TEXT)
    second = write_text(ctx.project_dir / "paper2.txt", PAPER_TEXT_2)
    result = paper_reader.PaperCompareTool().run(ctx, papers=[str(first), str(second)])
    assert result.ok
    table = result.data["table"]
    assert "| 论文 | 方法 | 数据集 | 指标 | 局限 |" in table
    assert "Diffusion Transformers for Video Generation" in table
    assert "Autoregressive Video Models" in table
    assert "UCF101" in table and "Kinetics" in table
    out_path = Path(result.data["path"])
    assert out_path.is_file()
    assert out_path.parent == ctx.project_dir / "notes"
    assert str(out_path) in result.artifacts


def test_paper_compare_requires_two_papers(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    text_path = write_text(ctx.project_dir / "paper1.txt", PAPER_TEXT)
    result = paper_reader.PaperCompareTool().run(ctx, papers=[str(text_path)])
    assert not result.ok
    assert "至少需要 2 篇" in result.error


# ---------------------------------------------------------------------------
# 真实网络测试（默认跳过）
# ---------------------------------------------------------------------------

integration = pytest.mark.integration
requires_network = pytest.mark.skipif(
    not os.environ.get("SCIFORGE_RUN_INTEGRATION"),
    reason="真实网络测试默认跳过；设置 SCIFORGE_RUN_INTEGRATION=1 启用",
)


@integration
@requires_network
def test_arxiv_real_network() -> None:
    records = literature.arxiv_search("diffusion transformer", limit=2)
    assert records
    assert records[0].title


@integration
@requires_network
def test_daily_real_network() -> None:
    records = literature.daily_arxiv_search(limit=2)
    assert records
    assert records[0].title
