"""论文精读工具（T1）：PDF 解析 → 结构化章节 → 精读笔记 / 问答 / 多篇对比。

设计要点：

- PDF 文本提取优先 ``pymupdf``，缺失或失败时兜底 ``pypdf``；两者都失败给出可操作建议
  （扫描件需 OCR、可换 HTML/arXiv 版本、手动提供文本）；
- 章节切分基于标题正则（Abstract / Introduction / Related Work / Method / Experiments /
  Conclusion / References / Appendix），对编号标题、中英文标题均有覆盖；
- LLM 增强是**可选**的：优先用 ``ctx.extra["router"]``，否则用配置构造 ``LLMRouter``；
  没有 API key / 调用失败时自动回退到确定性离线笔记，绝不让精读流程中断；
- 精读笔记同时写入 ``<project>/notes/<paper_id>.md`` 与文献库内 ``library/notes/``；
- ``paper_ask`` 在离线模式下返回全文分块检索片段（而非报错）；
- ``add_cli(app)`` 挂 ``sciforge read ...``（§14.6）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from sciforge.core.registry import BaseTool, ToolContext, ToolResult, register_tool
from sciforge.library import PaperLibrary, PaperRecord, safe_id
from sciforge.tools.literature import (
    LiteratureError,
    cli_context,
    resolve_local_pdf,
)

console = Console()


class PaperReadError(RuntimeError):
    """论文读取/解析失败（消息面向用户可操作）。"""


# ---------------------------------------------------------------------------
# 领域词表（用于离线启发式笔记/对比）
# ---------------------------------------------------------------------------

DATASETS: tuple[str, ...] = (
    "ImageNet",
    "ImageNet-1K",
    "ImageNet-21K",
    "COCO",
    "MS-COCO",
    "VOC",
    "ADE20K",
    "Cityscapes",
    "CIFAR-10",
    "CIFAR-100",
    "Kinetics",
    "UCF101",
    "HMDB51",
    "ActivityNet",
    "Something-Something",
    "Ego4D",
    "nuScenes",
    "KITTI",
    "Waymo",
    "ScanNet",
    "NYU",
    "LibriSpeech",
    "AudioSet",
    "VQAv2",
    "GQA",
    "TextVQA",
    "ScienceQA",
    "MMLU",
    "GSM8K",
    "MATH",
    "HumanEval",
    "MBPP",
    "BBH",
    "HellaSwag",
    "Winogrande",
    "ARC",
    "TruthfulQA",
    "PIQA",
    "GLUE",
    "SuperGLUE",
    "SQuAD",
    "WMT",
    "MS MARCO",
    "LAION",
    "Visual Genome",
    "LVIS",
    "Objects365",
    "FFHQ",
    "CelebA",
    "Places365",
    "OpenImages",
    "iNaturalist",
    "VTAB",
    "WikiText",
    "The Pile",
)

METRICS: tuple[str, ...] = (
    "mIoU",
    "IoU",
    "FID",
    "CLIP score",
    "CLIP-Score",
    "BLEU",
    "ROUGE",
    "METEOR",
    "CIDEr",
    "SPICE",
    "top-1 accuracy",
    "top-5 accuracy",
    "accuracy",
    "precision",
    "recall",
    "F1",
    "AUC",
    "AP",
    "mAP",
    "PSNR",
    "SSIM",
    "LPIPS",
    "perplexity",
    "pass@1",
    "pass@k",
    "exact match",
    "EM",
    "win rate",
    "ELO",
    "success rate",
    "return",
)

TECHNIQUE_KEYWORDS: tuple[str, ...] = (
    "attention",
    "transformer",
    "diffusion",
    "denoising",
    "contrastive learning",
    "self-supervised",
    "distillation",
    "LoRA",
    "adapter",
    "prompt tuning",
    "RLHF",
    "DPO",
    "mixture of experts",
    "MoE",
    "graph neural network",
    "GNN",
    "reinforcement learning",
    "reward model",
    "chain-of-thought",
    "retrieval-augmented generation",
    "RAG",
    "world model",
    "3D reconstruction",
    "NeRF",
    "Gaussian splatting",
    "flow matching",
    "autoregressive",
    "quantization",
    "pruning",
)

_SECTION_LABELS = {
    "preamble": "题名/作者/前言",
    "abstract": "摘要",
    "intro": "引言",
    "related": "相关工作",
    "method": "方法",
    "experiments": "实验与结果",
    "conclusion": "结论",
    "references": "参考文献",
    "appendix": "附录",
    "body": "正文",
    "summary": "AI 摘要",
}

_SECTION_ORDER = [
    "abstract",
    "intro",
    "related",
    "method",
    "experiments",
    "conclusion",
    "appendix",
    "references",
]

_STOPWORDS = set(
    """
    the a an of and or to in on for with is are was were be been being this that these those
    we our it its by as at from can could will would should may might not no than then there
    their they them he she his her you your i me my us also such which who whom what when where
    why how all any both each few more most other some only own same so too very just do does
    did doing have has had having but if while into over under between among during through
    about against before after above below up down out off again further once here
    的 了 和 与 及 或 在 是 为 对 从 到 中 上 下 而 也 都 就 还 被 把 让 使 可以 我们 本文 该 这 那
    一个 一种 以及 通过 进行 提出 研究 方法 结果 实验 论文
    """.split()
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,}|[\u4e00-\u9fff]{2,}")


# ---------------------------------------------------------------------------
# PDF / 文本提取
# ---------------------------------------------------------------------------


def _load_pymupdf() -> Any:
    try:
        import pymupdf  # type: ignore[import-not-found]

        return pymupdf
    except ImportError:
        try:
            import fitz  # type: ignore[import-not-found]

            return fitz
        except ImportError:
            return None


def _extract_with_pymupdf(path: Path) -> tuple[str, str, int] | None:
    """返回 (文本, 元数据标题, 页数)；未安装返回 None，解析异常抛 PaperReadError。"""
    module = _load_pymupdf()
    if module is None:
        return None
    try:
        document = module.open(str(path))
    except Exception as exc:  # noqa: BLE001 - 第三方库异常类型不稳定
        raise PaperReadError(f"pymupdf 打开 PDF 失败：{exc}") from exc
    try:
        pages = [page.get_text("text") or "" for page in document]
        metadata = document.metadata or {}
        page_count = int(getattr(document, "page_count", len(pages)) or len(pages))
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass
    return "\n\n".join(pages).strip(), str(metadata.get("title") or "").strip(), page_count


def _extract_with_pypdf(path: Path) -> tuple[str, str, int] | None:
    """用 pypdf 兜底提取；未安装返回 None，解析异常抛 PaperReadError。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        metadata = reader.metadata or {}
        title = str(getattr(metadata, "title", "") or "").strip()
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise PaperReadError(f"pypdf 解析 PDF 失败：{exc}") from exc
    return "\n\n".join(pages).strip(), title, page_count


def extract_text_from_pdf(path: Path) -> tuple[str, str, str, int]:
    """提取 PDF 全文，返回 ``(文本, 提取器, 元数据标题, 页数)``。"""
    path = Path(path)
    if not path.is_file():
        raise PaperReadError(f"文件不存在：{path}")
    errors: list[str] = []
    for name, extractor in (("pymupdf", _extract_with_pymupdf), ("pypdf", _extract_with_pypdf)):
        try:
            result = extractor(path)
        except PaperReadError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - 防御第三方库异常
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if result is None:
            errors.append(f"{name}: 未安装")
            continue
        text, title, pages = result
        if text.strip():
            return text, name, title, pages
        errors.append(f"{name}: 未提取到文本（可能是扫描版 PDF）")
    raise PaperReadError(
        "PDF 文本提取失败："
        + "；".join(errors)
        + "\n建议：1) 该 PDF 可能是扫描版，先 OCR（如 `ocrmypdf in.pdf out.pdf`）后重试；"
        "2) 提供可复制文本的 arXiv/HTML 版本；3) 安装/升级 pymupdf：`pip install pymupdf`。"
    )


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def extract_text(path: Path) -> tuple[str, str, str, int]:
    """读取 PDF / txt / md / html 文本，返回 ``(文本, 提取器, 标题, 页数)``。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PaperReadError(f"读取文件失败：{path}（{exc}）") from exc
    if suffix in {".html", ".htm"}:
        raw = _strip_html(raw)
    if not raw.strip():
        raise PaperReadError(f"文件内容为空：{path}")
    return raw, f"plain-{suffix.lstrip('.') or 'text'}", "", 0


# ---------------------------------------------------------------------------
# 章节切分
# ---------------------------------------------------------------------------

_HEADING_SPECS: list[tuple[str, re.Pattern[str]]] = [
    ("abstract", re.compile(r"^(?:abstract|摘\s*要)\b[.:：—\-]?", re.IGNORECASE)),
    (
        "intro",
        re.compile(
            r"^(?:(?:\d{1,2}|[IVX]{1,4})[.、)]?\s*)?(?:introduction|引\s*言|绪\s*论)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "related",
        re.compile(
            r"^(?:(?:\d{1,2}|[IVX]{1,4})[.、)]?\s*)?"
            r"(?:related\s+works?|background|相关工作|研究背景)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "method",
        re.compile(
            r"^(?:(?:\d{1,2}(?:\.\d{1,2})?|[IVX]{1,4})[.、)]?\s*)?"
            r"(?:method(?:s|ology)?|approach|proposed\s+(?:method|framework|model|approach)|"
            r"our\s+(?:method|approach|framework)|方法|模型|本文方法)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "experiments",
        re.compile(
            r"^(?:(?:\d{1,2}(?:\.\d{1,2})?|[IVX]{1,4})[.、)]?\s*)?"
            r"(?:experiments?(?:\s+and\s+(?:results|analysis|discussion))?|"
            r"experimental\s+(?:results|setup|evaluation|section)|"
            r"results(?:\s+and\s+(?:analysis|discussion))?|evaluation|implementations?|"
            r"实验(?:与结果|结果|设置)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "conclusion",
        re.compile(
            r"^(?:(?:\d{1,2}|[IVX]{1,4})[.、)]?\s*)?"
            r"(?:conclusions?(?:\s+and\s+future\s+work)?|concluding\s+remarks|"
            r"discussion\s+and\s+conclusions?|结论|总结)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "references",
        re.compile(
            r"^(?:(?:\d{1,2}|[IVX]{1,4})[.、)]?\s*)?(?:references|bibliography|参考文献)\s*$",
            re.IGNORECASE,
        ),
    ),
    ("appendix", re.compile(r"^(?:appendix|附录)[\sA-Z0-9.、:：-]*$", re.IGNORECASE)),
]


def _match_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return None
    if stripped.endswith((".", ";", ",", "?")) and not re.match(
        r"^(?:abstract|摘要)", stripped, re.IGNORECASE
    ):
        return None
    words = re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]", stripped)
    if len(words) > 12:
        return None
    for key, pattern in _HEADING_SPECS:
        if pattern.match(stripped):
            return key
    return None


def _clean_block(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sections(text: str) -> dict[str, str]:
    """按常见论文章节标题切分全文，返回 ``{章节: 正文}``。

    识别 Abstract / Introduction / Related Work / Method / Experiments / Conclusion /
    References / Appendix（含编号与中文标题）；识别不到任何章节时回退 ``{"body": 全文}``。
    """
    blocks: dict[str, list[str]] = {"preamble": []}
    order: list[str] = ["preamble"]
    current = "preamble"
    for raw_line in str(text or "").splitlines():
        key = _match_heading(raw_line)
        if key is not None:
            if key == current:  # 页眉/页脚重复标题，忽略
                continue
            current = key
            if key not in blocks:
                blocks[key] = []
                order.append(key)
            continue
        blocks[current].append(raw_line)
    result: dict[str, str] = {}
    for key in order:
        content = _clean_block("\n".join(blocks.get(key, [])))
        if content:
            result[key] = content
    if not any(key not in ("preamble", "body") for key in result):
        return {"body": _clean_block(str(text or ""))} if str(text or "").strip() else {}
    return result


def extract_title(text: str, *, metadata_title: str = "") -> str:
    """优先用 PDF 元数据标题，否则取正文前几行的论文标题。"""
    candidate = re.sub(r"\s+", " ", str(metadata_title or "")).strip()
    if candidate and len(candidate) > 3 and not re.match(
        r"(?i)^(untitled|microsoft word|draft|paper|document)", candidate
    ):
        return candidate[:200]
    for line in str(text or "").splitlines()[:40]:
        line = re.sub(r"\s+", " ", line).strip()
        if not line or len(line) < 10 or len(line) > 220:
            continue
        if re.match(r"(?i)^arxiv:", line):
            continue
        if re.match(r"(?i)^(abstract|摘要|keywords?|索引词)", line):
            break
        if line.count(" ") < 2:
            continue
        if re.search(r"@|university|institute|department|laboratory", line, re.IGNORECASE):
            continue
        return line
    return ""


# ---------------------------------------------------------------------------
# 分块检索 / 关键词抽取
# ---------------------------------------------------------------------------


def _terms(text: str) -> list[str]:
    tokens = [token.lower() for token in _TOKEN_RE.findall(str(text or ""))]
    return [token for token in tokens if len(token) > 1 and token not in _STOPWORDS]


def chunk_text(text: str, *, size: int = 1200, overlap: int = 150) -> list[str]:
    """按段落把全文切成 ~size 字符的分块（长段落带 overlap 滑窗）。"""
    text = _clean_block(str(text or ""))
    if not text:
        return []
    size = max(200, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > size:
            if current:
                chunks.append(current)
                current = ""
            step = max(1, size - overlap)
            for start in range(0, len(paragraph), step):
                piece = paragraph[start : start + size].strip()
                if piece:
                    chunks.append(piece)
                if start + size >= len(paragraph):
                    break
            continue
        if len(current) + len(paragraph) + 2 <= size:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            if current:
                chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def retrieve_chunks(chunks: list[str], question: str, *, top_k: int = 5) -> list[tuple[float, str]]:
    """极简关键词检索：返回 ``[(score, chunk)]``，用于 paper_ask 的原文定位。"""
    query_terms = set(_terms(question))
    scored: list[tuple[float, str]] = []
    for chunk in chunks:
        tokens = _terms(chunk)
        if not tokens:
            continue
        if query_terms:
            overlap = query_terms.intersection(tokens)
            if not overlap:
                continue
            score = sum(tokens.count(term) for term in overlap) / (len(tokens) ** 0.5)
        else:
            score = 0.0
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[: max(1, int(top_k))]


def _detect_terms(text: str, candidates: tuple[str, ...], *, limit: int = 5) -> list[str]:
    lower = str(text or "").lower()
    found = [term for term in candidates if term.lower() in lower]
    return found[:limit]


def _find_method_statement(text: str, *, limit: int = 2) -> str:
    pattern = re.compile(
        r"[^.!?。]*(?:we\s+(?:propose|introduce|present|develop|design|build)|"
        r"this\s+(?:paper|work)\s+(?:proposes|introduces|presents)|"
        r"我们(?:提出|设计|引入)|本文(?:提出|设计|引入))[^.!?。]*[.!?。]?",
        re.IGNORECASE,
    )
    statements = [re.sub(r"\s+", " ", match.group(0)).strip() for match in pattern.finditer(str(text or ""))]
    statements = [item for item in statements if len(item) > 20]
    return " ".join(statements[:limit]).strip()


def _find_limitations(text: str, *, limit: int = 3) -> list[str]:
    pattern = re.compile(
        r"[^.!?。]*(?:limitation|future\s+work|remains?\s+(?:an?\s+)?(?:open|challenging)|"
        r"不足|局限|未来工作)[^.!?。]*[.!?。]?",
        re.IGNORECASE,
    )
    found: list[str] = []
    for match in pattern.finditer(str(text or "")):
        sentence = re.sub(r"\s+", " ", match.group(0)).strip()
        if 15 <= len(sentence) <= 400 and sentence not in found:
            found.append(sentence)
        if len(found) >= limit:
            break
    return found


def _find_key_numbers(text: str, *, limit: int = 8) -> list[str]:
    numbers = re.findall(r"\b\d+(?:\.\d+)?\s?%", str(text or ""))
    result: list[str] = []
    for number in numbers:
        normalized = number.replace(" ", "")
        if normalized not in result:
            result.append(normalized)
    return result[:limit]


# ---------------------------------------------------------------------------
# LLM（可选，优雅降级）
# ---------------------------------------------------------------------------


def _get_router(ctx: ToolContext) -> Any:
    router = ctx.extra.get("router") or ctx.extra.get("llm_router")
    if router is not None:
        return router
    try:
        from sciforge.llm.router import LLMRouter
    except Exception:  # noqa: BLE001 - llm 模块缺失时纯离线
        return None
    try:
        candidate = LLMRouter(ctx.settings)
    except Exception:  # noqa: BLE001
        return None
    if getattr(candidate, "force_mock", False):
        return candidate
    try:
        api_key = ctx.settings.resolve_api_key()
    except Exception:  # noqa: BLE001 - 配置缺失按无 key 处理
        api_key = None
    return candidate if api_key else None


def _llm_text(
    ctx: ToolContext,
    prompt: str,
    *,
    system: str | None = None,
    role: str = "executor",
) -> str | None:
    router = _get_router(ctx)
    if router is None:
        return None
    try:
        text = router.complete(role, prompt, system=system)
    except Exception as exc:  # noqa: BLE001 - 任何 LLM 失败都回退离线
        ctx.log(f"LLM 调用失败，回退离线模式：{exc}", level="warn")
        return None
    text = str(text or "").strip()
    return text or None


# ---------------------------------------------------------------------------
# 论文装载
# ---------------------------------------------------------------------------


@dataclass
class _PaperText:
    record: PaperRecord
    text: str
    sections: dict[str, str]
    title: str
    path: Path | None = None
    extractor: str = ""
    pages: int = 0
    metadata_only: bool = False
    attempts: list[str] = field(default_factory=list)

    @property
    def body_sections(self) -> dict[str, str]:
        """去掉参考文献后的正文章节（用于问答/对比）。"""
        return {key: value for key, value in self.sections.items() if key != "references"}


def _load_paper(
    ctx: ToolContext,
    identifier: str,
    *,
    allow_metadata_only: bool = False,
) -> _PaperText:
    """解析标识 → 本地文件 → 全文/章节。失败抛 PaperReadError。"""
    client = ctx.extra.get("http_client")
    try:
        path, record, attempts = resolve_local_pdf(ctx, identifier, client=client)
    except Exception as exc:  # noqa: BLE001 - resolve 内部已降级，这里防御
        raise PaperReadError(f"论文定位失败：{exc}") from exc
    if path is None:
        if allow_metadata_only and record is not None and (record.abstract or record.summary):
            text = "\n\n".join(part for part in (record.summary, record.abstract) if part)
            sections = {"abstract": record.abstract} if record.abstract else {"summary": record.summary}
            return _PaperText(
                record=record,
                text=text,
                sections=sections,
                title=record.title or record.id,
                extractor="metadata-only",
                metadata_only=True,
                attempts=attempts,
            )
        raise PaperReadError(
            "无法获取论文全文。\n尝试记录：\n"
            + "\n".join(f"- {item}" for item in attempts)
            + "\n建议：用 paper_fetch 下载 PDF，或手动下载后把本地路径传给本工具。"
        )

    text, extractor, metadata_title, pages = extract_text(path)
    title = extract_title(text, metadata_title=metadata_title)
    library = PaperLibrary(ctx.data_dir)
    if record is None:
        record = PaperRecord(
            id=safe_id(path.stem),
            title=title or path.stem,
            local_pdf=str(path),
            source="manual",
            tags=["manual"],
        )
    else:
        record.local_pdf = str(path)
    if not record.title:
        record.title = title or path.stem
    stored = library.add(record)
    stored.local_pdf = stored.local_pdf or str(path)
    library.add(stored)
    record = stored
    return _PaperText(
        record=record,
        text=text,
        sections=split_sections(text),
        title=record.title or title,
        path=path,
        extractor=extractor,
        pages=pages,
        attempts=attempts,
    )


# ---------------------------------------------------------------------------
# 笔记生成
# -------------------------------------------------------------------------------------------------------------------------------------


def _note_header(paper: _PaperText) -> str:
    record = paper.record
    lines = [f"# {paper.title or record.id}", ""]
    lines.append(f"> 论文精读笔记 · SciForge · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    meta = [f"论文 ID：`{record.id}`"]
    if record.authors:
        meta.append(f"作者：{', '.join(record.authors[:8])}" + (" et al." if len(record.authors) > 8 else ""))
    if record.published:
        meta.append(f"发表：{record.published[:10]}")
    if record.venue:
        meta.append(f"会议/期刊：{record.venue}")
    if record.categories:
        meta.append(f"分类：{', '.join(record.categories[:3])}")
    link = record.best_url or record.pdf_url
    if link:
        meta.append(f"链接：{link}")
    if paper.path is not None:
        meta.append(f"本地文件：`{paper.path}`")
    meta.append(f"提取方式：{paper.extractor or '未知'}")
    if paper.metadata_only:
        meta.append("⚠️ 仅元数据（未读取 PDF 全文）")
    lines.extend(f"- {item}" for item in meta)
    return "\n".join(lines)


def _offline_note_body(paper: _PaperText) -> str:
    record = paper.record
    sections = paper.sections
    abstract = record.abstract or sections.get("abstract", "")
    summary = record.summary or _first_sentence(abstract) or "（缺少摘要，建议核对原文）"
    method_text = sections.get("method", "")
    experiments_text = sections.get("experiments", "")
    conclusion_text = sections.get("conclusion", "")
    intro_text = sections.get("intro", "")
    full_text = paper.text
    datasets = _detect_terms(full_text, DATASETS)
    metrics = _detect_terms(full_text, METRICS)
    techniques = _detect_terms(full_text, TECHNIQUE_KEYWORDS)
    limitations = _find_limitations(conclusion_text or full_text) or [
        "原文未显式讨论局限；建议重点核对实验覆盖范围、baseline 强度与泛化性。"
    ]
    numbers = _find_key_numbers(experiments_text or full_text)
    problem = _first_sentences(abstract, 2) or _shorten(intro_text, 500) or "（需人工补充研究问题）"
    method_sentence = (
        _find_method_statement(abstract)
        or _find_method_statement(method_text)
        or _shorten(method_text, 500)
        or "（需人工补充方法核心）"
    )
    lines: list[str] = ["", "## 一句话总结", "", _shorten(summary, 600), ""]
    lines += ["## 1. 研究问题与动机", "", problem or "（需人工补充）", ""]
    lines += [
        "## 2. 方法核心",
        "",
        method_sentence,
        "",
    ]
    if method_text:
        lines += [f"> 方法节选：{_shorten(method_text, 700)}", ""]
    lines += ["## 3. 实验与结果", ""]
    experiment_bullets: list[str] = []
    if datasets:
        experiment_bullets.append(f"数据集：{', '.join(datasets)}")
    if metrics:
        experiment_bullets.append(f"指标：{', '.join(metrics)}")
    if numbers:
        experiment_bullets.append(f"关键数字：{', '.join(numbers)}")
    if experiment_bullets:
        lines.extend(f"- {bullet}" for bullet in experiment_bullets)
    else:
        lines.append("- 未从全文中识别到明显的数据集/指标，需人工核对实验章节。")
    if experiments_text:
        lines += ["", f"> 实验节选：{_shorten(experiments_text, 700)}"]
    lines += ["", "## 4. 批判性分析（局限与可疑点）", ""]
    lines.extend(f"- {item}" for item in limitations)
    lines += [
        "- 自查清单：baseline 是否覆盖最强方法？是否报告方差/多种子？"
        "消融是否支撑每个设计选择？评测协议是否与宣称贡献匹配？",
        "",
        "## 5. 可迁移点",
        "",
    ]
    if techniques:
        lines.append(
            f"- 涉及技术：{', '.join(techniques)}；可评估迁移到自己的任务/数据规模下是否成立。"
        )
    lines.append("- 可复用组件/思路：请结合方法节选与代码链接判断工程可复现性。")
    lines.append("- 与当前课题的结合点：需结合你的研究问题人工确认（建议用 LitSearch → Ideation 流程）。")
    lines += [
        "",
        "## 6. 关键片段（原文）",
        "",
        f"> {_shorten(method_text or abstract or paper.text, 500).replace(chr(10), ' ')}",
        "",
        "*本笔记由 SciForge 离线启发式生成；使用 `paper_note llm=true`（需配置 LLM API key）可获得更强的批判性分析。*",
    ]
    return "\n".join(lines)


def _llm_note_body(ctx: ToolContext, paper: _PaperText, *, focus: str | None = None) -> str | None:
    record = paper.record
    sections = paper.sections
    abstract = record.abstract or sections.get("abstract", "")
    prompt_parts = [
        f"论文标题：{paper.title}",
        f"论文标识：{record.id}",
        f"作者：{', '.join(record.authors[:10])}",
        f"发表信息：{record.published} {record.venue}".strip(),
        "",
        "## Abstract",
        _shorten(abstract, 2500),
        "",
        "## Introduction（节选）",
        _shorten(sections.get("intro", ""), 1500),
        "",
        "## Method（节选）",
        _shorten(sections.get("method", ""), 3500),
        "",
        "## Experiments（节选）",
        _shorten(sections.get("experiments", ""), 2500),
        "",
        "## Conclusion（节选）",
        _shorten(sections.get("conclusion", ""), 1200),
    ]
    if focus:
        prompt_parts += ["", f"额外关注点：{focus}"]
    prompt_parts += [
        "",
        "请输出结构化精读笔记（Markdown），严格包含以下二级标题：",
        "## 一句话总结",
        "## 1. 研究问题与动机",
        "## 2. 方法核心（关键模块/公式/训练目标）",
        "## 3. 实验与结果（数据集、指标、关键数字、对比结论）",
        "## 4. 批判性分析（局限、可疑实验设置、缺失的 baseline）",
        "## 5. 可迁移点（如何与新的研究问题结合、可复用组件）",
        "## 6. 值得追读的参考文献/线索",
        "",
        "要求：引用原文关键数字与术语；不确定处标注“需核对原文”；总计 800-1500 字；不要复述提示词。",
    ]
    body = _llm_text(
        ctx,
        "\n".join(prompt_parts),
        system="你是资深深度学习论文审稿人，擅长把论文提炼成可直接用于科研决策的结构化精读笔记。",
        role="reviewer",
    )
    if not body:
        return None
    if "## 一句话总结" not in body:
        body = "## 一句话总结\n\n" + body
    return "\n" + body.strip() + "\n"


def _build_note(
    ctx: ToolContext,
    paper: _PaperText,
    *,
    llm: bool,
    focus: str | None = None,
) -> tuple[str, bool]:
    """生成笔记 Markdown，返回 ``(markdown, used_llm)``。"""
    header = _note_header(paper)
    if llm:
        body = _llm_note_body(ctx, paper, focus=focus)
        if body:
            return header + body, True
        header += (
            "\n\n> 未检测到可用 LLM（缺少 API key 或调用失败），已自动回退离线启发式笔记。"
            "配置 `DEEPSEEK_API_KEY` 后可获得更强分析。\n"
        )
    return header + _offline_note_body(paper), False


def _persist_note(ctx: ToolContext, record: PaperRecord, markdown: str) -> Path:
    """把笔记写入 ``<project>/notes/<paper_id>.md``，并同步登记到文献库。"""
    notes_dir = ctx.project_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    project_path = notes_dir / f"{safe_id(record.id)}.md"
    project_path.write_text(markdown, encoding="utf-8")
    library = PaperLibrary(ctx.data_dir)
    try:
        library.save_note(record.id, markdown)  # 库内副本
    except (OSError, ValueError) as exc:  # pragma: no cover - 库写入失败不阻塞项目笔记
        ctx.log(f"文献库笔记写入失败：{exc}", level="warn")
    record.notes_path = str(project_path)
    library.update(record)
    return project_path


def _first_sentence(text: str) -> str:
    return _first_sentences(text, 1)


def _first_sentences(text: str, count: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？.!?])\s+", text)
    return " ".join(parts[:count]).strip()


def _shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


# ---------------------------------------------------------------------------
# Tool：paper_read / paper_note / paper_ask / paper_compare
# ---------------------------------------------------------------------------


SECOND_LEVEL_SECTIONS = ("abstract", "intro", "related", "method", "experiments", "conclusion", "appendix")


class PaperReadTool(BaseTool):
    name = "paper_read"
    description = (
        "读取论文全文并结构化预览：PDF 用 pymupdf（兜底 pypdf）提取文本，按 "
        "Abstract/Intro/Related/Method/Experiments/Conclusion 切分章节，返回各章字数与预览。"
        "何时用：拿到本地 PDF / arXiv ID 后快速理解论文结构，决定读哪几节；"
        "需要完整精读笔记用 paper_note，针对某点提问用 paper_ask。llm=true 时同时生成精读笔记。"
    )
    tags = ("paper",)
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "paper": {
                "type": "string",
                "description": "本地 PDF/txt 路径、arXiv ID（2401.00001）、DOI 或 URL。",
            },
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "只看这些章节（可选）：abstract/intro/related/method/experiments/conclusion。",
            },
            "preview_chars": {
                "type": "integer",
                "description": "每节预览字符数，默认 400。",
            },
            "llm": {
                "type": "boolean",
                "description": "是否用 LLM 生成并保存精读笔记（无 key 自动回退离线版），默认 false。",
            },
        },
        "required": ["paper"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        identifier = str(self.require(kwargs, "paper")).strip()
        preview_chars = max(80, min(int(kwargs.get("preview_chars") or 400), 4000))
        try:
            paper = _load_paper(ctx, identifier)
        except (PaperReadError, LiteratureError) as exc:
            return ToolResult.failure(f"论文读取失败：{exc}")
        wanted = self._wanted_sections(kwargs.get("sections"))
        artifacts: list[str] = []
        if paper.path is not None:
            artifacts.append(str(paper.path))
        lines = [f"# 论文：{paper.title or paper.record.id}", ""]
        meta = [
            f"- 论文 ID：`{paper.record.id}`",
            f"- 提取方式：{paper.extractor}",
            f"- 页数：{paper.pages or '（文本文件）'}",
            f"- 字符数：{len(paper.text)}",
        ]
        if paper.path is not None:
            meta.append(f"- 本地文件：`{paper.path}`")
        lines.extend(meta)
        lines += ["", "## 章节结构", ""]
        section_data: dict[str, Any] = {}
        for key, content in paper.sections.items():
            label = _SECTION_LABELS.get(key, key)
            section_data[key] = {"chars": len(content), "preview": _shorten(content, preview_chars)}
            lines.append(f"- `{key}`（{label}）：{len(content)} 字符")
        preview_keys = [key for key in wanted if key in paper.sections] or [
            key for key in paper.sections if key in _SECTION_ORDER
        ]
        for key in preview_keys:
            content = paper.sections.get(key, "")
            if not content:
                continue
            label = _SECTION_LABELS.get(key, key)
            lines += ["", f"## {label}（预览）", "", _shorten(content, preview_chars)]
        lines += [
            "",
            "## 下一步",
            f"- 生成结构化精读笔记：`paper_note paper={paper.record.id}`",
            f"- 针对某点提问：`paper_ask paper={paper.record.id} question=...`",
            "- 多篇对比：`paper_compare papers=[...]`",
        ]
        data: dict[str, Any] = {
            "paper_id": paper.record.id,
            "title": paper.title,
            "path": str(paper.path) if paper.path is not None else "",
            "extractor": paper.extractor,
            "pages": paper.pages,
            "chars": len(paper.text),
            "sections": section_data,
            "metadata_only": paper.metadata_only,
        }
        llm_flag = bool(kwargs.get("llm"))
        if llm_flag:
            markdown, used_llm = _build_note(ctx, paper, llm=True)
            note_path = _persist_note(ctx, paper.record, markdown)
            artifacts.append(str(note_path))
            lines += ["", "## 精读笔记（已保存）", "", _shorten(markdown, 6000), "", f"笔记文件：`{note_path}`"]
            data["notes_path"] = str(note_path)
            data["llm_used"] = used_llm
        return ToolResult.success("\n".join(lines), data=data, artifacts=artifacts)

    @staticmethod
    def _wanted_sections(raw: Any) -> list[str]:
        if not raw:
            return list(SECOND_LEVEL_SECTIONS)
        if isinstance(raw, str):
            raw = [part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()]
        aliases = {
            "introduction": "intro",
            "related_work": "related",
            "related work": "related",
            "methods": "method",
            "experiment": "experiments",
            "results": "experiments",
            "conclusions": "conclusion",
            "abstract": "abstract",
        }
        wanted: list[str] = []
        for item in raw:
            key = aliases.get(str(item).strip().lower(), str(item).strip().lower())
            if key and key not in wanted:
                wanted.append(key)
        return wanted


class PaperNoteTool(BaseTool):
    name = "paper_note"
    description = (
        "生成/更新论文结构化精读笔记（元信息/研究问题/方法/实验/批判性分析/可迁移点），"
        "保存到 <project>/notes/<paper_id>.md 并登记到文献库。"
        "何时用：读透一篇论文、为创新点寻找可迁移组件、写 related work 前整理理解时。"
        "默认尝试 LLM 增强；无 API key 时自动生成确定性离线笔记（仍含摘要/方法/数据集/指标/局限）。"
    )
    tags = ("paper", "literature")
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "paper": {
                "type": "string",
                "description": "本地 PDF/txt 路径、arXiv ID、DOI 或 URL。",
            },
            "llm": {
                "type": "boolean",
                "description": "是否用 LLM 增强（默认 true；无 key 自动回退离线）。",
            },
            "focus": {
                "type": "string",
                "description": "额外关注点，如“训练稳定性”“数据效率”（可选，仅 LLM 模式生效）。",
            },
        },
        "required": ["paper"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        identifier = str(self.require(kwargs, "paper")).strip()
        use_llm = bool(kwargs.get("llm", True))
        focus = kwargs.get("focus")
        try:
            paper = _load_paper(ctx, identifier, allow_metadata_only=True)
        except (PaperReadError, LiteratureError) as exc:
            return ToolResult.failure(f"精读笔记生成失败：{exc}")
        markdown, used_llm = _build_note(ctx, paper, llm=use_llm, focus=str(focus) if focus else None)
        try:
            note_path = _persist_note(ctx, paper.record, markdown)
        except OSError as exc:
            return ToolResult.failure(f"笔记写入失败：{exc}。请检查项目目录写权限。")
        mode = "LLM 增强" if used_llm else "离线启发式（配置 LLM API key 可增强）"
        content = (
            f"精读笔记已生成：{note_path}\n"
            f"- 论文：{paper.title or paper.record.id}\n"
            f"- 模式：{mode}\n"
            f"- 字符数：{len(markdown)}\n\n{_shorten(markdown, 8000)}"
        )
        return ToolResult.success(
            content,
            data={
                "paper_id": paper.record.id,
                "title": paper.title,
                "note_path": str(note_path),
                "markdown": markdown,
                "llm_used": used_llm,
                "metadata_only": paper.metadata_only,
            },
            artifacts=[str(note_path)],
        )


class PaperAskTool(BaseTool):
    name = "paper_ask"
    description = (
        "针对某篇论文自由问答：先按关键词从全文分块检索相关段落，再让 LLM 基于原文回答并给出来源片段。"
        "何时用：想快速确认论文细节（损失函数、超参、数据规模、对比结论）而不必通读全文。"
        "无 LLM 时返回检索到的原文片段（离线可用）。"
    )
    tags = ("paper",)
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "paper": {
                "type": "string",
                "description": "本地 PDF/txt 路径、arXiv ID、DOI 或 URL。",
            },
            "question": {"type": "string", "description": "问题，如“训练用了多少 GPU、batch size 是多少？”"},
            "top_k": {"type": "integer", "description": "检索片段数，默认 5。"},
            "llm": {"type": "boolean", "description": "是否用 LLM 总结回答，默认 true（无 key 自动回退片段）。"},
        },
        "required": ["paper", "question"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        identifier = str(self.require(kwargs, "paper")).strip()
        question = str(self.require(kwargs, "question")).strip()
        top_k = max(1, min(int(kwargs.get("top_k") or 5), 20))
        try:
            paper = _load_paper(ctx, identifier)
        except (PaperReadError, LiteratureError) as exc:
            return ToolResult.failure(f"论文读取失败：{exc}")
        body = paper.body_sections or paper.sections
        chunks: list[str] = []
        for content in body.values():
            chunks.extend(chunk_text(content))
        ranked = retrieve_chunks(chunks, question, top_k=top_k)
        if not ranked and chunks:
            ranked = [(0.0, chunk) for chunk in chunks[:top_k]]
        snippets = [{"score": round(score, 4), "text": chunk} for score, chunk in ranked]
        answer: str | None = None
        mode = "offline-retrieval"
        if bool(kwargs.get("llm", True)) and snippets:
            context = "\n\n---\n\n".join(
                f"[片段 {index}]\n{snippet['text']}" for index, snippet in enumerate(snippets, 1)
            )
            prompt = (
                f"论文标题：{paper.title}\n\n"
                f"以下是论文全文中最相关的片段：\n\n{context}\n\n"
                f"问题：{question}\n\n"
                "请只依据上述片段回答，先用一两句话给出直接答案，再列出依据的片段编号与关键原文；"
                "如果片段不足以回答，明确说“原文片段不足以回答”，并说明还需要查哪一节。"
            )
            answer = _llm_text(
                ctx,
                prompt,
                system="你是严谨的论文阅读助手，只依据给定原文回答，不编造数字。",
                role="executor",
            )
            if answer:
                mode = "llm"
        lines = [f"# 论文问答：{paper.title or paper.record.id}", "", f"**问题**：{question}", ""]
        if answer:
            lines += ["## 回答", "", answer, ""]
        else:
            lines += [
                "## 回答",
                "",
                "（离线模式：未配置 LLM API key，返回全文检索片段。配置 `DEEPSEEK_API_KEY` 后可生成综合回答。）",
                "",
            ]
        lines += ["## 相关原文片段", ""]
        for index, snippet in enumerate(snippets, 1):
            lines.append(f"**[片段 {index}]**（相关度 {snippet['score']}）")
            lines.append("")
            lines.append(f"> {_shorten(snippet['text'], 900).replace(chr(10), ' ')}")
            lines.append("")
        if not snippets:
            lines.append("（未检索到相关内容，可能是问题关键词过于宽泛或全文提取失败。）")
        return ToolResult.success(
            "\n".join(lines),
            data={
                "paper_id": paper.record.id,
                "question": question,
                "mode": mode,
                "answer": answer,
                "snippets": snippets,
            },
        )


class PaperCompareTool(BaseTool):
    name = "paper_compare"
    description = (
        "多篇论文对比表：自动抽取每篇的 方法/数据集/指标/局限（启发式 + 可选 LLM），"
        "输出 Markdown 表格并保存到 <project>/notes/comparison-*.md。"
        "何时用：写 related work、确定创新点差异、选择 baseline 时做横向对比。"
        "离线可用；llm=true 时额外生成一段对比结论。"
    )
    tags = ("paper", "writing")
    requires = ()
    parameters = {
        "type": "object",
        "properties": {
            "papers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "论文标识列表（本地路径/arXiv ID/DOI），至少 2 篇。",
            },
            "output": {
                "type": "string",
                "description": "输出 md 路径（相对项目目录），默认 notes/comparison-<时间戳>.md。",
            },
            "llm": {"type": "boolean", "description": "是否额外用 LLM 生成对比结论，默认 false。"},
        },
        "required": ["papers"],
    }

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        raw = kwargs.get("papers")
        if isinstance(raw, str):
            raw = [raw]
        identifiers = [str(item).strip() for item in raw or [] if str(item).strip()]
        if len(identifiers) < 2:
            return ToolResult.failure("paper_compare 至少需要 2 篇论文，例如 papers=[\"2401.00001\", \"2402.00002\"]。")
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for identifier in identifiers:
            try:
                paper = _load_paper(ctx, identifier)
            except (PaperReadError, LiteratureError) as exc:
                errors.append(f"{identifier}: {exc}")
                rows.append(
                    {
                        "paper_id": identifier,
                        "title": identifier,
                        "method": "（读取失败）",
                        "datasets": "-",
                        "metrics": "-",
                        "limitations": _shorten(str(exc), 120),
                    }
                )
                continue
            record = paper.record
            abstract = record.abstract or paper.sections.get("abstract", "")
            method = (
                _find_method_statement(abstract, limit=1)
                or _shorten(paper.sections.get("method", ""), 220)
                or "（未识别，见原文方法节）"
            )
            datasets = _detect_terms(paper.text, DATASETS, limit=4)
            metrics = _detect_terms(paper.text, METRICS, limit=4)
            limitations = _find_limitations(
                paper.sections.get("conclusion", "") or paper.text, limit=2
            )
            rows.append(
                {
                    "paper_id": record.id,
                    "title": paper.title or record.title or record.id,
                    "method": _shorten(method, 220),
                    "datasets": ", ".join(datasets) or "-",
                    "metrics": ", ".join(metrics) or "-",
                    "limitations": _shorten(" ".join(limitations), 260) or "原文未显式讨论",
                }
            )
        table_lines = [
            "| 论文 | 方法 | 数据集 | 指标 | 局限 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            cells = [
                f"**{_cell(row['title'])}**<br>`{_cell(row['paper_id'])}`",
                _cell(row["method"]),
                _cell(row["datasets"]),
                _cell(row["metrics"]),
                _cell(row["limitations"]),
            ]
            table_lines.append("| " + " | ".join(cells) + " |")
        table = "\n".join(table_lines)
        analysis = ""
        if bool(kwargs.get("llm")) and len(rows) >= 2:
            prompt = (
                "以下是多篇论文的自动抽取信息（可能不完整）：\n\n"
                + "\n\n".join(
                    f"论文 {index}：{row['title']}\n方法：{row['method']}\n"
                    f"数据集：{row['datasets']}\n指标：{row['metrics']}\n局限：{row['limitations']}"
                    for index, row in enumerate(rows, 1)
                )
                + "\n\n请用中文写一段 200-300 字的对比结论：方法差异、评测协议是否可比、"
                "哪篇更适合作为 baseline、潜在创新空间。只依据给定信息，不编造。"
            )
            analysis = _llm_text(
                ctx,
                prompt,
                system="你是论文审稿人，擅长横向对比方法差异与评测公平性。",
                role="reviewer",
            ) or ""
        title = f"# 论文对比（{len(rows)} 篇）"
        markdown = title + "\n\n" + table + "\n"
        if analysis:
            markdown += "\n## 对比结论\n\n" + analysis.strip() + "\n"
        if errors:
            markdown += "\n## 读取失败\n\n" + "\n".join(f"- {item}" for item in errors) + "\n"
        if len([row for row in rows if not row["method"].startswith("（读取失败）")]) == 0:
            return ToolResult.failure(
                "所有论文都读取失败，无法生成对比表。\n" + "\n".join(errors)
            )
        output_value = kwargs.get("output")
        if output_value:
            output_path = Path(str(output_value)).expanduser()
            if not output_path.is_absolute():
                output_path = ctx.project_dir / output_path
        else:
            output_path = (
                ctx.project_dir / "notes" / f"comparison-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
            )
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"对比表写入失败：{exc}")
        content = f"已生成 {len(rows)} 篇论文对比表：{output_path}\n\n{markdown}"
        return ToolResult.success(
            content,
            data={
                "papers": [row["paper_id"] for row in rows],
                "rows": rows,
                "table": table,
                "analysis": analysis,
                "path": str(output_path),
                "failed": errors,
            },
            artifacts=[str(output_path)],
        )


def _cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


register_tool(PaperReadTool())
register_tool(PaperNoteTool())
register_tool(PaperAskTool())
register_tool(PaperCompareTool())


# ---------------------------------------------------------------------------
# CLI（§14.6：sciforge read ...）
# ---------------------------------------------------------------------------


def _print_result(result: ToolResult, *, json_output: bool) -> None:
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
    """挂载 ``sciforge read <paper> [--note] [--llm] [--ask]`` 子命令。"""

    @app.command("read")
    def read_cmd(
        paper: str = typer.Argument(..., help="arXiv ID / DOI / 本地 PDF 路径 / URL"),
        note: bool = typer.Option(False, "--note", help="同时生成结构化精读笔记"),
        llm: bool = typer.Option(False, "--llm", help="用 LLM 增强精读（无 key 自动离线）"),
        ask: str | None = typer.Option(None, "--ask", help="针对论文自由问答，如 --ask '损失函数是什么'"),
        json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
    ) -> None:
        """精读论文：结构化章节预览 / 生成笔记 / 自由问答。"""
        ctx = cli_context()
        if ask:
            result = PaperAskTool().run(ctx, paper=paper, question=ask, llm=llm)
            _print_result(result, json_output=json_output)
            return
        result = PaperReadTool().run(ctx, paper=paper, llm=llm)
        _print_result(result, json_output=json_output)
        if note:
            note_result = PaperNoteTool().run(ctx, paper=paper, llm=llm)
            _print_result(note_result, json_output=json_output)


__all__ = [
    "DATASETS",
    "METRICS",
    "PaperAskTool",
    "PaperCompareTool",
    "PaperNoteTool",
    "PaperReadError",
    "PaperReadTool",
    "add_cli",
    "chunk_text",
    "extract_text",
    "extract_text_from_pdf",
    "extract_title",
    "retrieve_chunks",
    "split_sections",
]
