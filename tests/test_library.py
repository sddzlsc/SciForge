"""PaperLibrary / PaperRecord 单元测试（完全离线，临时目录隔离）。

覆盖：CRUD、JSONL 持久化、重复合并、标签/搜索排序、PDF/笔记文件、ID 规范化、坏索引容错。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sciforge.library import PaperLibrary, PaperRecord, safe_id


def make_record(paper_id: str = "2401.00001", **overrides) -> PaperRecord:
    data = {
        "id": paper_id,
        "title": "Diffusion Transformers for Video Generation",
        "authors": ["Alice Smith", "Bob Chen"],
        "abstract": "We propose a diffusion transformer for video generation.",
        "url": f"https://arxiv.org/abs/{paper_id}",
        "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
        "published": "2024-01-02",
        "venue": "CVPR 2024",
        "categories": ["cs.CV"],
        "summary": "",
        "tags": ["diffusion"],
        "source": "arxiv",
    }
    data.update(overrides)
    return PaperRecord(**data)


@pytest.fixture()
def library(tmp_path: Path) -> PaperLibrary:
    return PaperLibrary(tmp_path / "data")


# ---------------------------------------------------------------------------
# CRUD / 持久化
# ---------------------------------------------------------------------------


def test_add_and_get_returns_record(library: PaperLibrary) -> None:
    record = library.add(make_record())
    fetched = library.get("2401.00001")
    assert fetched is not None
    assert fetched.id == "2401.00001"
    assert fetched.title == record.title
    assert fetched.added_at  # 自动填充 ISO8601
    assert len(library) == 1
    assert "2401.00001" in library


def test_get_missing_returns_none(library: PaperLibrary) -> None:
    assert library.get("nothing") is None
    assert library.get("2401.00001") is None


def test_persistence_across_instances(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    first = PaperLibrary(data_dir)
    first.add(make_record())
    first.save_note("2401.00001", "# 笔记")

    second = PaperLibrary(data_dir)
    assert len(second) == 1
    fetched = second.get("2401.00001")
    assert fetched is not None
    assert fetched.title.startswith("Diffusion Transformers")
    assert second.note("2401.00001") == "# 笔记"
    assert Path(fetched.notes_path).is_file()


def test_jsonl_storage_is_one_json_per_line(library: PaperLibrary) -> None:
    library.add(make_record())
    lines = library.index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["id"] == "2401.00001"
    assert payload["authors"] == ["Alice Smith", "Bob Chen"]


def test_corrupted_index_lines_are_skipped(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    index = data_dir / "library" / "library.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    valid = json.dumps(make_record().to_dict(), ensure_ascii=False)
    index.write_text(valid + "\n{not json}\n\n" + json.dumps({"id": "x"}) + "\n", encoding="utf-8")
    library = PaperLibrary(data_dir)
    assert library.count() == 1
    assert library.get("2401.00001") is not None


def test_update_replaces_record(library: PaperLibrary) -> None:
    record = library.add(make_record())
    record.title = "Updated Title"
    record.tags = ["updated"]
    library.update(record)
    fetched = library.get("2401.00001")
    assert fetched is not None
    assert fetched.title == "Updated Title"
    assert fetched.tags == ["updated"]
    reloaded = PaperLibrary(library.data_dir)
    assert reloaded.get("2401.00001").title == "Updated Title"


def test_remove_record(library: PaperLibrary) -> None:
    library.add(make_record())
    assert library.remove("2401.00001") is True
    assert library.get("2401.00001") is None
    assert library.remove("2401.00001") is False


def test_declared_arxiv_version_is_normalized(library: PaperLibrary) -> None:
    record = PaperRecord(id="2401.00001v3", title="Versioned")
    library.add(record)
    assert library.get("2401.00001") is not None
    assert library.get("2401.00001v3") is not None  # 查询侧也会去版本号
    assert library.get("arXiv:2401.00001v2") is not None
    assert library.get("2401.00001").arxiv_id == "2401.00001"


def test_doi_and_url_lookup_variants(library: PaperLibrary) -> None:
    doi = "10.1234/abc.def"
    library.add(PaperRecord(id=doi, title="DOI Paper"))
    assert library.get("https://doi.org/10.1234/abc.def") is not None
    assert library.get("doi:10.1234/abc.def") is not None
    assert library.get("10.1234/abc.def") is not None


# ---------------------------------------------------------------------------
# 合并 / 列表 / 搜索
# ---------------------------------------------------------------------------


def test_add_merges_duplicate_and_preserves_paths(library: PaperLibrary) -> None:
    first = library.add(make_record(tags=["diffusion"]))
    note_path = library.save_note(first.id, "# note")
    longer_abstract = first.abstract + " It also improves FVD by 12.5%."
    new = make_record(
        title="", abstract=longer_abstract, tags=["survey", "diffusion"], venue=""
    )
    merged = library.add(new)
    assert merged.id == "2401.00001"
    assert merged.title.startswith("Diffusion Transformers")  # 空标题不覆盖已有
    assert merged.abstract == longer_abstract  # 更完整的新摘要覆盖
    assert set(merged.tags) == {"diffusion", "survey"}  # 标签并集
    assert merged.notes_path == str(note_path)  # 本地路径保留
    assert len(library) == 1


def test_list_order_limit_and_tag_filter(library: PaperLibrary) -> None:
    library.add(make_record("2401.00001", title="A", tags=["cv"]))
    library.add(make_record("2402.00002", title="B", tags=["nlp"]))
    library.add(make_record("2403.00003", title="C", tags=["cv", "survey"]))
    all_records = library.list()
    assert len(all_records) == 3
    assert {record.title for record in all_records} == {"A", "B", "C"}
    assert len(library.list(limit=2)) == 2
    cv_records = library.list(tag="cv")
    assert {record.title for record in cv_records} == {"A", "C"}
    assert library.list(tag="missing") == []
    assert set(library.tags()) == {"cv", "nlp", "survey"}


def test_search_ranks_title_higher_than_abstract(library: PaperLibrary) -> None:
    library.add(
        make_record(
            "2401.00001",
            title="Diffusion Transformers for Video",
            abstract="A video generation method.",
        )
    )
    library.add(
        make_record(
            "2402.00002",
            title="Video Generation Survey",
            abstract="We review diffusion models and transformers for video.",
        )
    )
    results = library.search("diffusion")
    assert [record.id for record in results] == ["2401.00001", "2402.00002"]
    assert library.search("nonexistent-word") == []
    assert library.search("") == library.list(limit=20)


def test_search_matches_authors_and_tags(library: PaperLibrary) -> None:
    library.add(make_record("2401.00001", authors=["Yann LeCun"], tags=["self-supervised"]))
    assert library.search("LeCun")[0].id == "2401.00001"
    assert library.search("self-supervised")[0].id == "2401.00001"


# ---------------------------------------------------------------------------
# 文件
# ---------------------------------------------------------------------------


def test_save_pdf_writes_file_and_updates_record(library: PaperLibrary) -> None:
    library.add(make_record())
    path = library.save_pdf("2401.00001", b"%PDF-1.4 fake")
    assert path.is_file()
    assert path.read_bytes() == b"%PDF-1.4 fake"
    assert path.parent == library.pdfs_dir
    record = library.get("2401.00001")
    assert record is not None
    assert record.local_pdf == str(path)


def test_save_note_and_read_note(library: PaperLibrary) -> None:
    library.add(make_record())
    path = library.save_note("2401.00001", "# 精读笔记\n\n内容")
    assert path.is_file()
    assert path.parent == library.notes_dir
    assert library.note("2401.00001") == "# 精读笔记\n\n内容"
    record = library.get("2401.00001")
    assert record is not None
    assert record.notes_path == str(path)


def test_note_missing_returns_none(library: PaperLibrary) -> None:
    assert library.note("2401.00001") is None
    library.add(make_record())
    assert library.note("2401.00001") is None


def test_note_fallback_by_safe_id(library: PaperLibrary) -> None:
    library.add(PaperRecord(id="10.1234/abc.def", title="DOI"))
    manual = library.notes_dir / f"{safe_id('10.1234/abc.def')}.md"
    manual.parent.mkdir(parents=True, exist_ok=True)
    manual.write_text("manual note", encoding="utf-8")
    assert library.note("10.1234/abc.def") == "manual note"


# ---------------------------------------------------------------------------
# 记录行为
# ---------------------------------------------------------------------------


def test_record_normalizes_fields() -> None:
    record = PaperRecord(
        id="2401.00001",
        title="  A   Title \n with whitespace ",
        authors="Authors: Alice, Bob and Carol",
        categories=["cs.CV", "cs.CV"],
        published="2024-01-02T00:00:00Z",
        tags=["x", "x"],
    )
    assert record.title == "A Title with whitespace"
    assert record.authors == ["Alice", "Bob", "Carol"]
    assert record.categories == ["cs.CV"]
    assert record.tags == ["x"]
    assert record.published == "2024-01-02"
    assert record.year == "2024"
    assert record.short_authors == "Alice, Bob, Carol"
    assert record.best_url.endswith("/2401.00001")
    assert record.arxiv_id == "2401.00001"


def test_record_from_dict_ignores_unknown_fields() -> None:
    payload = {
        "id": "2401.00001",
        "title": "T",
        "authors": ["A"],
        "unknown_field": "ignored",
        "source": "s2",
    }
    record = PaperRecord.from_dict(payload)
    assert record.id == "2401.00001"
    assert record.source == "s2"
    assert "unknown_field" not in record.to_dict()


def test_record_requires_id(library: PaperLibrary) -> None:
    with pytest.raises(ValueError):
        library.add(PaperRecord(id="", title="no id"))
    with pytest.raises(TypeError):
        library.add({"id": "x"})  # type: ignore[arg-type]


def test_safe_id_handles_doi_and_urls() -> None:
    assert safe_id("10.1234/abc.def") == "10.1234_abc.def"
    assert safe_id("http://arxiv.org/abs/2401.00001v2") == "arxiv.org_abs_2401.00001v2"
    assert safe_id("") == "paper"
    assert safe_id("../evil path") == "evil-path"
    # 结果可安全用作文件名
    assert "/" not in safe_id("10.1234/abc") and "\\" not in safe_id("10.1234/abc")


def test_empty_library_behaviour(tmp_path: Path) -> None:
    library = PaperLibrary(tmp_path / "empty")
    assert library.count() == 0
    assert library.all() == []
    assert library.list() == []
    assert library.search("anything") == []
    assert library.tags() == []
    assert library.note("x") is None
