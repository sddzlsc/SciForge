"""论文写作出口：PaperProject / BibTeX / 审稿式自检。"""

from __future__ import annotations

from sciforge.writing import citations, paper, review
from sciforge.writing.citations import (
    CitationError,
    add_bibtex,
    collect_from_arxiv,
    collect_from_doi,
    dedupe_bibtex,
    merge_bibtex,
)
from sciforge.writing.paper import PaperProject, create_paper_project, tex_escape_text
from sciforge.writing.review import rule_review, self_review

__all__ = [
    "citations",
    "paper",
    "review",
    "PaperProject",
    "CitationError",
    "create_paper_project",
    "tex_escape_text",
    "add_bibtex",
    "collect_from_arxiv",
    "collect_from_doi",
    "dedupe_bibtex",
    "merge_bibtex",
    "rule_review",
    "self_review",
]
