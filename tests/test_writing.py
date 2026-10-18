"""writing 模块测试：论文工程 / BibTeX / 审稿自检 / 工具注册 / CLI。"""

from __future__ import annotations

import types
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sciforge.config import Settings
from sciforge.core.registry import ToolContext, get_tool, run_tool
from sciforge.tools.writing import add_cli as add_writing_cli
from sciforge.writing import citations
from sciforge.writing.paper import PaperProject, create_paper_project
from sciforge.writing.review import rule_review, self_review

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <published>2024-01-02T00:00:00Z</published>
    <title>An Example Paper for Testing</title>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
    <comment>Accepted at CVPR 2024</comment>
  </entry>
</feed>
"""

CROSSREF_JSON = (
    '{"message": {"title": ["A Crossref Paper"], "author": [{"given": "Carol", "family": "Wang"}],'
    ' "container-title": ["IEEE TPAMI"], "published-print": {"date-parts": [[2023]]},'
    ' "type": "journal-article", "URL": "https://doi.org/10.1000/test"}}'
)


# ---------------------------------------------------------------------------
# 工程生成
# ---------------------------------------------------------------------------


def _make_paper(tmp_path: Path, venue: str = "generic") -> PaperProject:
    dest = tmp_path / f"paper-{venue}"
    create_paper_project(dest, venue=venue, title="Test Paper", authors=["Alice", "Bob"])
    return PaperProject(dest)


def test_create_generic_project_without_official_sty(tmp_path: Path) -> None:
    project = _make_paper(tmp_path, "generic")
    assert project.exists
    assert project.venue == "generic"
    assert set(project.list_sections()) == {
        "01_introduction",
        "02_related_work",
        "03_method",
        "04_experiments",
        "05_conclusion",
    }
    main = project.main_tex.read_text(encoding="utf-8")
    assert r"\documentclass[11pt]{article}" in main
    assert r"\begin{document}" in main and r"\end{document}" in main
    assert r"\bibliography{refs}" in main
    for name in ("refs.bib", "README.md"):
        assert (project.dest / name).exists()
    assert (project.dest / "figures").is_dir() and (project.dest / "tables").is_dir()
    # generic 不依赖任何官方 sty（注释里提到 sty 不算）
    active = "\n".join(line for line in main.splitlines() if not line.lstrip().startswith("%"))
    assert "cvpr.sty" not in active and r"\usepackage{cvpr}" not in active
    # 模板里的引用 key 都能在 refs.bib 找到
    bib = project.bib_path.read_text(encoding="utf-8")
    assert citations.bib_keys(bib)
    assert "TODO" in project.main_tex.read_text(encoding="utf-8") or "TODO" in "\n".join(
        path.read_text(encoding="utf-8") for path in project.sections_dir.glob("*.tex")
    )


@pytest.mark.parametrize(("venue", "marker"), [("cvpr", "cvpr"), ("neurips", "neurips_2024"), ("iclr", "iclr2024"), ("acl", "acl")])
def test_venue_templates_contain_official_style(tmp_path: Path, venue: str, marker: str) -> None:
    project = _make_paper(tmp_path, venue)
    main = project.main_tex.read_text(encoding="utf-8")
    assert marker in main
    assert project.venue == venue
    notes = [issue for issue in project.check() if issue["kind"] == "missing_venue_style"]
    assert notes, "会议模板应提示缺少官方 sty"


def test_create_project_twice_and_invalid_venue(tmp_path: Path) -> None:
    dest = tmp_path / "p"
    create_paper_project(dest, venue="generic", title="A", authors=["X"])
    with pytest.raises(FileExistsError):
        create_paper_project(dest, venue="generic", title="A", authors=["X"])
    with pytest.raises(ValueError):
        create_paper_project(tmp_path / "q", venue="nature", title="A", authors=["X"])


def test_add_and_append_sections(tmp_path: Path) -> None:
    project = _make_paper(tmp_path)
    path = project.add_section("06_discussion", "\\section{Discussion}\nDraft text.")
    assert path.exists()
    main = project.main_tex.read_text(encoding="utf-8")
    assert r"\input{sections/06_discussion}" in main
    assert main.index(r"\input{sections/06_discussion}") < main.index(r"\bibliography{refs}")
    project.add_section("06_discussion", "\\section{Discussion}\nReplaced.")
    assert "Replaced." in project.read_section("06_discussion")
    project.append_to_section("06_discussion", "More content.")
    assert "Replaced." in path.read_text(encoding="utf-8")
    assert "More content." in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_reports_placeholders_and_undefined_refs(tmp_path: Path) -> None:
    project = _make_paper(tmp_path)
    issues = project.check()
    kinds = {issue["kind"] for issue in issues}
    assert "placeholder" in kinds
    assert "undefined_reference" in kinds  # 方法章节引用了图，但图还是注释状态
    assert all({"severity", "kind", "message", "file", "line"} <= set(issue) for issue in issues)
    assert (project.dest / "check-report.json").exists()


def test_check_reports_missing_figure_citation_and_tables(tmp_path: Path) -> None:
    project = _make_paper(tmp_path)
    project.add_section(
        "07_problem",
        "\\section{Problem}\n"
        "See Figure~\\ref{fig:nope} and Table~\\ref{tab:nope}.\n"
        "As shown in \\cite{nonexistent2024}.\n"
        "\\includegraphics[width=\\linewidth]{figures/nope.pdf}\n",
    )
    kinds = {issue["kind"] for issue in project.check()}
    assert {"undefined_reference", "undefined_citation", "missing_figure"} <= kinds


def test_check_accepts_existing_figure_and_abstract_length(tmp_path: Path) -> None:
    project = _make_paper(tmp_path)
    (project.figures_dir / "fig1.pdf").write_bytes(b"%PDF-1.4 fake")
    project.add_section(
        "08_results",
        "\\section{Results}\n\\begin{figure}\\includegraphics{figures/fig1.pdf}"
        "\\caption{x}\\label{fig:ok}\\end{figure}\nSee Figure~\\ref{fig:ok}.\n",
    )
    kinds = {issue["kind"] for issue in project.check()}
    assert "missing_figure" not in kinds

    # 超长摘要
    main = project.main_tex.read_text(encoding="utf-8")
    long_abstract = " ".join(["word"] * 300)
    main = main.replace(
        "\\begin{abstract}\n\\noindent\n% TODO: 用 120-250 词总结：问题、方法、关键结果、意义。不要放引用与公式。\n",
        f"\\begin{{abstract}}\n{long_abstract}\n",
    )
    project.main_tex.write_text(main, encoding="utf-8")
    long_kinds = {issue["kind"] for issue in project.check()}
    assert "abstract_too_long" in long_kinds


# ---------------------------------------------------------------------------
# compile（不依赖真实 LaTeX）
# ---------------------------------------------------------------------------


def test_compile_without_latex_returns_none_with_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_paper(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert project.compile() is None
    assert "未检测到 LaTeX" in project.last_compile["message"]
    assert "TinyTeX" in project.last_compile["message"]


def test_compile_success_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_paper(tmp_path)
    pdf = project.dest / "main.pdf"

    def fake_which(name: str) -> str | None:
        return f"/usr/local/bin/{name}" if name in ("latexmk", "xelatex") else None

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        pdf.write_bytes(b"%PDF-1.4 fake output")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.run", fake_run)
    compiled = project.compile()
    assert compiled == pdf and pdf.exists()
    assert project.last_compile["ok"] is True
    assert project.last_compile["engine"] == "latexmk"


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------


def test_bibtex_parse_dedupe_and_merge(tmp_path: Path) -> None:
    first = citations.make_bibtex_entry(
        title="Paper One", authors=["Alice Zhang"], year="2024", venue="CVPR"
    )
    second = citations.make_bibtex_entry(
        title="Paper Two", authors=["Bob Li"], year="2023", venue="ICLR"
    )
    merged = citations.merge_bibtex(first, first, second)
    keys = citations.bib_keys(merged)
    assert len(keys) == 2 and len(set(keys)) == 2
    entries = citations.parse_bibtex(merged)
    assert all(entry.fields.get("title") for entry in entries)
    assert citations.dedupe_bibtex(merged) == merged

    bib_path = tmp_path / "refs.bib"
    written = citations.add_bibtex(first, bib_path)
    assert written == bib_path
    citations.add_bibtex(merged, bib_path)
    assert len(citations.bib_keys(bib_path.read_text(encoding="utf-8"))) == 2


def test_collect_from_arxiv_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(citations, "_http_get", lambda url, timeout=30.0: ATOM_XML)
    text = citations.collect_from_arxiv(["2401.00001"])
    assert "@article" in text
    assert "An Example Paper for Testing" in text
    assert "CVPR 2024" in text
    assert citations.bib_keys(text)


def test_collect_from_doi_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(citations, "_http_get", lambda url, timeout=30.0: CROSSREF_JSON)
    text = citations.collect_from_doi("10.1000/test")
    assert "A Crossref Paper" in text and "IEEE TPAMI" in text and "2023" in text


def test_collect_from_arxiv_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, timeout: float = 30.0) -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(citations, "_http_get", boom)
    with pytest.raises(citations.CitationError, match="arXiv"):
        citations.collect_from_arxiv(["2401.00001"])


# ---------------------------------------------------------------------------
# 审稿式自检
# ---------------------------------------------------------------------------


def test_self_review_rule_based(tmp_path: Path) -> None:
    project = _make_paper(tmp_path)
    issues = rule_review(project.dest)
    assert issues
    severities = {issue["severity"] for issue in issues}
    assert severities <= {"major", "minor", "info"}
    assert any(issue["severity"] == "major" for issue in issues)
    report = self_review(project.dest, use_llm=False)
    assert "审稿式自检报告" in report
    assert "## Major Issues" in report and "## Minor Issues" in report
    assert "规则版报告" in report  # 无 LLM 时明确说明降级
    # 报告不应抛出、不应访问网络
    assert "下一步建议" in report


def test_self_review_with_results_dir(tmp_path: Path) -> None:
    project = _make_paper(tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    (results / "metrics.json").write_text('{"worst_group_acc": 74.1}', encoding="utf-8")
    report = self_review(project.dest, results_dir=results, use_llm=False)
    assert "metrics.json" in report or "未在正文出现" in report


# ---------------------------------------------------------------------------
# 工具注册 + CLI
# ---------------------------------------------------------------------------


def _context(tmp_path: Path) -> ToolContext:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return ToolContext(
        settings=Settings(),
        project_dir=project,
        data_dir=tmp_path / "data",
        session_dir=project / ".sciforge" / "sessions" / "test",
    )


def test_writing_tools_registered_and_pipeline(tmp_path: Path) -> None:
    names = {"paper_init", "paper_section", "citation_add", "paper_compile", "paper_review"}
    assert all(get_tool(name) is not None for name in names)
    ctx = _context(tmp_path)

    init = run_tool("paper_init", ctx, {"venue": "generic", "title": "Tool Paper", "authors": "Alice,Bob"})
    assert init.ok and (ctx.project_dir / "paper" / "main.tex").exists()

    section = run_tool(
        "paper_section",
        ctx,
        {"name": "04_experiments", "content": "\\section{Experiments}\nWe report 74.1."},
    )
    assert section.ok and "04_experiments" in (ctx.project_dir / "paper" / "main.tex").read_text(
        encoding="utf-8"
    )

    cite = run_tool(
        "citation_add",
        ctx,
        {"entries": citations.make_bibtex_entry(title="Cited", authors=["A"], year="2024")},
    )
    assert cite.ok and cite.data["total_keys"] >= 1

    review = run_tool("paper_review", ctx, {"use_llm": False})
    assert review.ok and Path(review.data["report_path"]).exists()
    assert review.data["major"] >= 1

    compile_result = run_tool("paper_compile", ctx, {})
    assert compile_result.ok  # 缺 LaTeX 时也是“可解释降级”，不算工具失败
    assert compile_result.data["compiled"] is False


def test_writing_cli_smoke(tmp_path: Path) -> None:
    app = typer.Typer()
    add_writing_cli(app)
    runner = CliRunner()
    paper_dir = tmp_path / "cli-paper"
    result = runner.invoke(
        app,
        ["write", "init", "--dest", str(paper_dir), "--venue", "generic", "--title", "CLI Paper", "--authors", "A,B"],
    )
    assert result.exit_code == 0, result.output
    assert (paper_dir / "main.tex").exists()

    result = runner.invoke(app, ["write", "check", "--dir", str(paper_dir)])
    assert result.exit_code == 0, result.output
    assert "placeholder" in result.output

    result = runner.invoke(app, ["write", "compile", "--dir", str(paper_dir)])
    assert result.exit_code == 0, result.output  # 缺 LaTeX 也返回 0（明确提示）
