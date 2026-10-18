"""本地文献库：:class:`PaperRecord` + :class:`PaperLibrary`（ARCHITECTURE §14.1）。

存储布局（``data_dir`` 即 ``~/.sciforge``，测试中可用 ``SCIFORGE_HOME`` 或临时目录隔离）::

    <data_dir>/library/
        library.jsonl     # 每条记录一行 JSON（新增/更新后原子重写）
        pdfs/<safe_id>.pdf
        notes/<safe_id>.md

设计要点：

- 所有写盘操作都是原子的（临时文件 + ``os.replace``），进程中断不会留下半个索引；
- 索引损坏或单行非法时跳过该行，不阻塞整个文献库加载；
- ``add`` 对同一 ``id`` 做合并：保留本地 PDF/笔记路径与标签并集，用新元数据补全空字段；
- 线程安全（``RLock``），便于 Web / Pipeline 多线程消费。

本模块不访问网络、不依赖配置，任何模块都可以直接 ``PaperLibrary(data_dir)`` 使用。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LIBRARY_DIR_NAME = "library"
INDEX_FILE_NAME = "library.jsonl"
NOTES_DIR_NAME = "notes"
PDFS_DIR_NAME = "pdfs"

_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*|[\u4e00-\u9fff]{2,}")
_ARXIV_ID_RE = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """当前本地时间（带时区）的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_id(paper_id: str) -> str:
    """把 arXiv ID / DOI / URL 转成安全的文件名片段。

    ``10.1234/abc.def`` → ``10.1234_abc.def``；``http://arxiv.org/abs/2401.00001v2``
    → ``arxiv.org_abs_2401.00001v2``。结果只包含 ``[A-Za-z0-9._-]``，可直接当文件名。
    """
    text = str(paper_id or "").strip()
    if not text:
        return "paper"
    text = re.sub(r"^arxiv:", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^https?://", "", text, flags=re.IGNORECASE)
    text = text.replace("/", "_").replace("\\", "_")
    text = _ID_SAFE_RE.sub("-", text)
    text = text.strip("-._")
    return text[:120] or "paper"


def _as_list(value: Any) -> list[str]:
    """把 ``None`` / 字符串 / 列表统一成去空的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        text = re.sub(r"^(?:authors?|作者)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
        parts = re.split(r"\s*(?:,|;|、|\band\b)\s*", text)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                candidate = item.get("name") or item.get("display_name") or ""
            else:
                candidate = item
            candidate = str(candidate).strip()
            if candidate:
                result.append(candidate)
        return result
    return [str(value).strip()] if str(value).strip() else []


def _clean_text(value: Any) -> str:
    """压缩空白（arXiv/Crossref 元数据里常有换行与连续空格）。"""
    return _WS_RE.sub(" ", str(value or "")).strip()


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------


@dataclass
class PaperRecord:
    """一篇文献的元数据（ARCHITECTURE §14.1，字段名不可改）。"""

    id: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    url: str = ""
    pdf_url: str = ""
    published: str = ""
    venue: str = ""
    categories: list[str] = field(default_factory=list)
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    local_pdf: str = ""
    notes_path: str = ""
    source: str = "arxiv"
    added_at: str = ""

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        # arXiv ID 统一去掉版本号（2401.00001v2 → 2401.00001），避免同一论文重复入库
        if _ARXIV_ID_RE.fullmatch(self.id):
            self.id = re.sub(r"v\d+$", "", self.id)
        self.title = _clean_text(self.title)
        self.authors = _as_list(self.authors)
        for name in (
            "abstract",
            "url",
            "pdf_url",
            "published",
            "venue",
            "summary",
            "local_pdf",
            "notes_path",
        ):
            setattr(self, name, _clean_text(getattr(self, name)))
        self.categories = _dedupe(_as_list(self.categories))
        self.tags = _dedupe(_as_list(self.tags))
        self.source = _clean_text(self.source) or "manual"
        self.added_at = str(self.added_at or "").strip() or now_iso()
        if self.published:
            self.published = self.published[:10] if len(self.published) >= 10 else self.published

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaperRecord:
        """从 dict 恢复记录；忽略未知字段（兼容旧版本索引）。"""
        if not isinstance(data, dict):
            raise TypeError(f"PaperRecord 需要 dict，实际是 {type(data).__name__}")
        allowed = {item.name for item in fields(cls)}
        payload = {key: value for key, value in data.items() if key in allowed}
        return cls(**payload)

    # -- 合并 --------------------------------------------------------------
    def merge_missing(self, other: PaperRecord) -> PaperRecord:
        """用 ``other`` 的新元数据补全/刷新本记录，保留本地路径与时间戳。

        规则：
        - ``id`` / ``added_at`` / ``local_pdf`` / ``notes_path`` 不覆盖已有值；
        - ``tags`` / ``categories`` 取并集；
        - 其余文本字段：新值非空且旧值为空才填；标题/摘要类元数据允许用更长的新值覆盖。
        """
        for item in fields(self):
            name = item.name
            if name in ("id", "added_at"):
                continue
            new_value = getattr(other, name)
            old_value = getattr(self, name)
            if name in ("local_pdf", "notes_path"):
                if new_value and not old_value:
                    setattr(self, name, new_value)
            elif name in ("tags", "categories"):
                setattr(self, name, _dedupe([*_as_list(old_value), *_as_list(new_value)]))
            elif isinstance(new_value, str):
                if not new_value:
                    continue
                if not old_value or (name in ("title", "abstract", "summary") and len(new_value) > len(old_value)):
                    setattr(self, name, new_value)
            elif new_value and not old_value:  # pragma: no cover - 预留扩展字段
                setattr(self, name, new_value)
        return self

    @property
    def year(self) -> str:
        """发表年份（用于 BibTeX / 展示），取不到时返回空串。"""
        return self.published[:4] if self.published[:4].isdigit() else ""

    @property
    def short_authors(self) -> str:
        """前 3 位作者 + et al.，用于检索结果展示。"""
        if not self.authors:
            return ""
        head = ", ".join(self.authors[:3])
        return f"{head} et al." if len(self.authors) > 3 else head

    @property
    def best_url(self) -> str:
        return self.url or self.pdf_url or (f"https://arxiv.org/abs/{self.id}" if self.arxiv_id else "")

    @property
    def arxiv_id(self) -> str | None:
        """若该记录是 arXiv 论文，返回去版本号的 arXiv ID。"""
        match = _ARXIV_ID_RE.fullmatch(self.id.strip())
        return re.sub(r"v\d+$", "", match.group(0)) if match else None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# 文献库
# ---------------------------------------------------------------------------


class PaperLibrary:
    """基于 JSONL 索引的本地文献库（线程安全、原子写、无网络依赖）。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.root = self.data_dir / LIBRARY_DIR_NAME
        self.index_path = self.root / INDEX_FILE_NAME
        self.notes_dir = self.root / NOTES_DIR_NAME
        self.pdfs_dir = self.root / PDFS_DIR_NAME
        self._lock = threading.RLock()
        self._records: dict[str, PaperRecord] = {}
        self._load()

    # -- 持久化 ------------------------------------------------------------
    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            text = self.index_path.read_text(encoding="utf-8")
        except OSError:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = PaperRecord.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue  # 单行损坏不影响其它记录
            if not record.id:
                continue
            existing = self._records.get(record.id)
            if existing is not None:
                existing.merge_missing(record)
            else:
                self._records[record.id] = record

    def _flush(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".library-", suffix=".jsonl.tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for record in self._records.values():
                    fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            os.replace(tmp_name, self.index_path)
        finally:
            if os.path.exists(tmp_name):  # pragma: no cover - 正常路径已被 replace
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    def _candidate_ids(self, paper_id: str) -> list[str]:
        raw = str(paper_id or "").strip()
        if not raw:
            return []
        candidates = [raw]
        stripped = re.sub(r"^arxiv:", "", raw, flags=re.IGNORECASE).strip()
        candidates.append(stripped)
        candidates.append(re.sub(r"v\d+$", "", stripped))
        candidates.append(re.sub(r"^https?://(?:dx\.)?doi\.org/", "", stripped, flags=re.IGNORECASE))
        candidates.append(re.sub(r"^doi:", "", stripped, flags=re.IGNORECASE).strip())
        result: list[str] = []
        for candidate in candidates:
            candidate = candidate.strip().rstrip("/")
            if candidate and candidate not in result:
                result.append(candidate)
        return result

    # -- CRUD --------------------------------------------------------------
    def add(self, record: PaperRecord) -> PaperRecord:
        """新增或合并一篇文献，返回库中实际存储（可能是合并后）的记录。"""
        if not isinstance(record, PaperRecord):
            raise TypeError(f"add 需要 PaperRecord，实际是 {type(record).__name__}")
        paper_id = record.id.strip()
        if not paper_id:
            raise ValueError("PaperRecord.id 不能为空（arXiv ID / DOI / S2 ID）")
        with self._lock:
            existing = self._records.get(paper_id)
            if existing is None:
                self._records[paper_id] = record
                self._flush()
                return record
            existing.merge_missing(record)
            self._flush()
            return existing

    def get(self, paper_id: str) -> PaperRecord | None:
        with self._lock:
            for candidate in self._candidate_ids(paper_id):
                record = self._records.get(candidate)
                if record is not None:
                    return record
            return None

    def update(self, record: PaperRecord) -> None:
        """整体替换同 id 记录（不存在则新增）。"""
        if not isinstance(record, PaperRecord):
            raise TypeError(f"update 需要 PaperRecord，实际是 {type(record).__name__}")
        if not record.id.strip():
            raise ValueError("PaperRecord.id 不能为空")
        with self._lock:
            self._records[record.id] = record
            self._flush()

    def remove(self, paper_id: str) -> bool:
        """删除一篇文献（不删除本地 PDF/笔记文件），返回是否删除成功。"""
        with self._lock:
            record = self.get(paper_id)
            if record is None:
                return False
            self._records.pop(record.id, None)
            self._flush()
            return True

    def all(self) -> list[PaperRecord]:
        """全部记录（按加入时间倒序）。"""
        with self._lock:
            records = list(self._records.values())
        return sorted(records, key=lambda r: (r.added_at, r.title), reverse=True)

    def list(self, *, limit: int = 50, tag: str | None = None) -> list[PaperRecord]:
        """按加入时间倒序列出文献，可按标签过滤。"""
        limit = max(1, int(limit or 50))
        records = self.all()
        if tag:
            wanted = tag.strip().casefold()
            records = [r for r in records if any(wanted == t.casefold() for t in r.tags)]
        return records[:limit]

    def search(self, query: str, *, limit: int = 20) -> list[PaperRecord]:
        """在标题/摘要/总结/作者/标签/分类上做关键词检索，按相关度排序。"""
        terms = [t.casefold() for t in _TOKEN_RE.findall(str(query or "").lower()) if len(t) > 1]
        if not terms:
            return self.list(limit=limit)
        limit = max(1, int(limit or 20))
        scored: list[tuple[float, PaperRecord]] = []
        for record in self._records.values():
            score = _score_record(record, terms)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].added_at), reverse=True)
        return [record for _, record in scored[:limit]]

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def tags(self) -> list[str]:
        """库中出现过的全部标签（按频次倒序）。"""
        counts: dict[str, int] = {}
        for record in self._records.values():
            for tag in record.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return [tag for tag, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    # -- 文件 --------------------------------------------------------------
    def save_pdf(self, paper_id: str, content: bytes) -> Path:
        """把 PDF 字节保存到库内 ``pdfs/``，并更新记录中的 ``local_pdf``。"""
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("save_pdf 需要 bytes")
        self.pdfs_dir.mkdir(parents=True, exist_ok=True)
        path = self.pdfs_dir / f"{safe_id(paper_id)}.pdf"
        path.write_bytes(bytes(content))
        self._attach_path(paper_id, local_pdf=str(path))
        return path

    def save_note(self, paper_id: str, markdown: str) -> Path:
        """把精读笔记保存到库内 ``notes/``，并更新记录中的 ``notes_path``。"""
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        path = self.notes_dir / f"{safe_id(paper_id)}.md"
        path.write_text(str(markdown or ""), encoding="utf-8")
        self._attach_path(paper_id, notes_path=str(path))
        return path

    def note(self, paper_id: str) -> str | None:
        """读取笔记正文；不存在时返回 ``None``。"""
        record = self.get(paper_id)
        candidates: list[Path] = []
        if record is not None and record.notes_path:
            candidates.append(Path(record.notes_path))
        candidates.append(self.notes_dir / f"{safe_id(paper_id)}.md")
        if record is not None:
            candidates.append(self.notes_dir / f"{safe_id(record.id)}.md")
        for path in candidates:
            try:
                if path.is_file():
                    return path.read_text(encoding="utf-8")
            except OSError:  # pragma: no cover - 文件系统异常
                continue
        return None

    def _attach_path(self, paper_id: str, **updates: str) -> None:
        with self._lock:
            record = self.get(paper_id)
            if record is None:
                return
            for name, value in updates.items():
                if value and not getattr(record, name, ""):
                    setattr(record, name, value)
                elif value and name in ("local_pdf", "notes_path"):
                    setattr(record, name, value)
            self._flush()

    # -- 便利方法 ----------------------------------------------------------
    def __len__(self) -> int:
        return self.count()

    def __contains__(self, paper_id: object) -> bool:
        return self.get(str(paper_id)) is not None

    def __repr__(self) -> str:  # pragma: no cover - 调试输出
        return f"PaperLibrary(root={self.root!s}, count={self.count()})"


def _score_record(record: PaperRecord, terms: list[str]) -> float:
    """轻量关键词相关度：标题 4x、总结/标签 3x、摘要 2x、作者/分类 1x。"""
    title = record.title.casefold()
    summary = f"{record.summary} {' '.join(record.tags)}".casefold()
    abstract = record.abstract.casefold()
    meta = f"{' '.join(record.authors)} {' '.join(record.categories)} {record.venue}".casefold()
    score = 0.0
    for term in terms:
        score += 8.0 if term in title else 0.0
        score += 4.0 if term in summary else 0.0
        score += 2.0 if term in abstract else 0.0
        score += 1.0 if term in meta else 0.0
    if not score and record.id.casefold() in " ".join(terms):
        score = 1.0
    return score


__all__ = [
    "LIBRARY_DIR_NAME",
    "INDEX_FILE_NAME",
    "NOTES_DIR_NAME",
    "PDFS_DIR_NAME",
    "PaperRecord",
    "PaperLibrary",
    "now_iso",
    "safe_id",
]
