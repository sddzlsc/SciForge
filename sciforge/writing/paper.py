"""LaTeX 论文工程管理：生成骨架、追加章节、自检、编译。

- :func:`create_paper_project`：从 Jinja2 模板生成本领域常用的完整论文工程
  （``cvpr`` / ``neurips`` / ``iclr`` / ``acl`` / ``generic``）；
- :class:`PaperProject`：``add_section`` / ``check`` / ``compile`` / ``stats``。

设计要点：
- ``generic`` 模板不依赖任何官方 sty，保证「没有 sty 也能编译」；会议模板在
  preamble 里注明需要下载哪个官方 sty；
- ``compile`` 只做环境探测与调用，缺 LaTeX 时返回 ``None`` 并给出安装提示，不抛异常；
- ``check`` 返回结构化 issue 列表（TODO/占位符、未定义引用、图表缺失、摘要过长等）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import jinja2

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "paper"
META_FILE = "paper.json"
MAIN_TEX = "main.tex"

#: 各 venue 的元信息：模板名、显示名、官方 sty、作者分隔符、说明
VENUES: dict[str, dict[str, Any]] = {
    "generic": {
        "label": "Generic (article)",
        "style_file": None,
        "note": "不需要官方 sty，article + 自定义格式即可直接编译。",
        "author_sep": r" \\ ",
        "citation_style": "natbib",
    },
    "cvpr": {
        "label": "CVPR",
        "style_file": "cvpr.sty",
        "note": "从 CVPR 官网 Author Kit 下载 cvpr.sty 放到 main.tex 同级目录。",
        "author_sep": r" \\ ",
        "citation_style": "natbib",
    },
    "neurips": {
        "label": "NeurIPS",
        "style_file": "neurips_2024.sty",
        "note": "从 NeurIPS 官网下载 neurips_2024.sty；投稿用 [preprint]，终稿用 [final]。",
        "author_sep": r" \And ",
        "citation_style": "natbib",
    },
    "iclr": {
        "label": "ICLR",
        "style_file": "iclr2024_conference.sty",
        "note": "从 ICLR 官网下载 iclr2024_conference.sty 与 .bst。",
        "author_sep": r" \And ",
        "citation_style": "natbib",
    },
    "acl": {
        "label": "ACL / EMNLP",
        "style_file": "acl.sty",
        "note": "从 *ACL 论文模板包下载 acl.sty 与 acl_natbib.bst。",
        "author_sep": r" \And ",
        "citation_style": "natbib",
    },
}

DEFAULT_SECTIONS: list[dict[str, str]] = [
    {"file": "01_introduction", "title": "Introduction"},
    {"file": "02_related_work", "title": "Related Work"},
    {"file": "03_method", "title": "Method"},
    {"file": "04_experiments", "title": "Experiments"},
    {"file": "05_conclusion", "title": "Conclusion"},
]

_TEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_PLACEHOLDER_RE = re.compile(
    r"\b(TODO|FIXME|XXX|PLACEHOLDER|TBD)\b|待补充|待完成|待写|占位",
    re.IGNORECASE,
)
_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{([^}]*)\}")
_REF_RE = re.compile(r"\\ref\{([^}]*)\}")
_LABEL_RE = re.compile(r"\\label\{([^}]*)\}")
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")
_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]*)\}")
_SECTION_RE = re.compile(r"\\section\*?\{([^}]*)\}")
_ABSTRACT_RE = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.S)
_BIB_ENTRY_RE = re.compile(r"@\s*\w+\s*\{\s*([^,\s]+)\s*,")

TODO_HINTS = (
    "TODO 处需要替换为真实内容（数字必须可追溯到 results/）；"
    "运行 `sciforge write review` 可列出全部待修项。"
)


def tex_escape_text(text: str) -> str:
    """转义标题/作者里的 LaTeX 特殊字符（保留中文）。"""
    return "".join(_TEX_SPECIALS.get(char, char) for char in str(text))


def _environment() -> jinja2.Environment:
    if not TEMPLATES_DIR.exists():
        raise FileNotFoundError(f"论文模板目录不存在：{TEMPLATES_DIR}")
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _render(env: jinja2.Environment, template_name: str, context: dict[str, Any]) -> str:
    template = env.get_template(template_name)
    return template.render(**context)


# ---------------------------------------------------------------------------
# 创建工程
# ---------------------------------------------------------------------------


def create_paper_project(
    dest: str | Path,
    *,
    venue: str = "generic",
    title: str = "",
    authors: list[str] | None = None,
) -> Path:
    """生成一个可编译的 LaTeX 论文工程，返回工程根目录。

    参数
    ----
    dest:    工程目录（不存在会自动创建）
    venue:   ``cvpr`` | ``neurips`` | ``iclr`` | ``acl`` | ``generic``
    title:   论文标题（写在 ``\\title{}``，特殊字符会自动转义）
    authors: 作者列表
    """
    venue_key = str(venue or "generic").strip().lower()
    if venue_key not in VENUES:
        raise ValueError(f"未知 venue={venue!r}，可用：{', '.join(VENUES)}")
    dest_path = Path(dest).expanduser()
    main_tex = dest_path / MAIN_TEX
    if main_tex.exists():
        raise FileExistsError(
            f"目录中已存在 main.tex：{dest_path}。"
            "请换一个目录，或用 PaperProject(dest).add_section(...) 修改现有工程。"
        )
    title = (title or "Untitled Paper").strip() or "Untitled Paper"
    authors = [str(author).strip() for author in (authors or []) if str(author).strip()]
    if not authors:
        authors = ["Anonymous Authors"]

    sections_dir = dest_path / "sections"
    for sub in ("sections", "figures", "tables"):
        (dest_path / sub).mkdir(parents=True, exist_ok=True)

    info = VENUES[venue_key]
    author_sep = str(info["author_sep"])
    authors_tex = author_sep.join(tex_escape_text(author) for author in authors)
    context: dict[str, Any] = {
        "title": title,
        "title_tex": tex_escape_text(title),
        "authors": authors,
        "authors_tex": authors_tex,
        "venue": venue_key,
        "venue_label": info["label"],
        "venue_note": info["note"],
        "citation_style": info["citation_style"],
        "date": r"\today",
        "sections": DEFAULT_SECTIONS,
        "include_appendix": True,
        "dest_name": dest_path.name,
    }
    env = _environment()

    # 各章节
    for section in DEFAULT_SECTIONS:
        content = _render(env, f"sections/{section['file']}.tex.j2", context)
        (sections_dir / f"{section['file']}.tex").write_text(content, encoding="utf-8")

    # main.tex
    (dest_path / MAIN_TEX).write_text(_render(env, "main.tex.j2", context), encoding="utf-8")
    # refs.bib
    (dest_path / "refs.bib").write_text(_render(env, "refs.bib.j2", context), encoding="utf-8")
    # 示例图表（不默认被 \input，供参考）
    (dest_path / "tables" / "table_example.tex").write_text(
        _render(env, "table_example.tex.j2", context), encoding="utf-8"
    )
    (dest_path / "figures" / "figure_example.tex").write_text(
        _render(env, "figure_example.tex.j2", context), encoding="utf-8"
    )
    # README
    (dest_path / "README.md").write_text(_render(env, "README.md.j2", context), encoding="utf-8")
    # 元信息（PaperProject / check / review 用）
    meta = {
        "venue": venue_key,
        "title": title,
        "authors": authors,
        "sections": DEFAULT_SECTIONS,
        "created_by": "sciforge.writing.paper",
        "version": 1,
    }
    (dest_path / META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest_path


# ---------------------------------------------------------------------------
# PaperProject
# ---------------------------------------------------------------------------


def _issue(
    severity: str,
    kind: str,
    message: str,
    *,
    file: str = "",
    line: int = 0,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "kind": kind,
        "message": message,
        "file": file,
        "line": line,
    }


def _word_count(text: str) -> int:
    """粗粒度词数：去掉 LaTeX 命令/注释后按空白切分（中文按字符近似）。"""
    cleaned = re.sub(r"(?<!\\)%.*", "", text)
    cleaned = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?", " ", cleaned)
    cleaned = re.sub(r"[{}$&#^_~]", " ", cleaned)
    tokens = cleaned.split()
    cjk = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
    return len(tokens) + cjk // 2


class PaperProject:
    """一个论文工程目录的读写/检查/编译接口。"""

    def __init__(self, dest: str | Path) -> None:
        self.dest = Path(dest).expanduser().resolve()
        self.main_tex = self.dest / MAIN_TEX
        self.sections_dir = self.dest / "sections"
        self.figures_dir = self.dest / "figures"
        self.tables_dir = self.dest / "tables"
        self.bib_path = self.dest / "refs.bib"
        self.meta_path = self.dest / META_FILE
        self.meta: dict[str, Any] = {}
        if self.meta_path.exists():
            try:
                self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.meta = {}
        #: 最近一次 compile 的结果（ok/message/engine/log/command/pdf）
        self.last_compile: dict[str, Any] = {}

    # ---- 构造 ----
    @classmethod
    def create(
        cls,
        dest: str | Path,
        venue: str = "generic",
        title: str = "",
        authors: list[str] | None = None,
    ) -> PaperProject:
        """便捷构造：``PaperProject.create(dest, "cvpr", "Title", ["A", "B"])``。"""
        create_paper_project(dest, venue=venue, title=title, authors=authors)
        return cls(dest)

    @property
    def exists(self) -> bool:
        return self.main_tex.exists()

    @property
    def venue(self) -> str:
        if self.meta.get("venue"):
            return str(self.meta["venue"])
        text = self._read(self.main_tex)
        for key in VENUES:
            if key != "generic" and f"{{{key}}}" in text:
                return key
        return "generic"

    # ---- 章节 ----
    def list_sections(self) -> list[str]:
        """列出现有章节文件名（不含 ``.tex``，按文件名排序）。"""
        if not self.sections_dir.exists():
            return []
        return sorted(path.stem for path in self.sections_dir.glob("*.tex"))

    def section_path(self, name: str) -> Path:
        filename = str(name).strip()
        if filename.endswith(".tex"):
            filename = filename[:-4]
        filename = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", filename).strip("_")
        if not filename:
            raise ValueError("章节名不能为空")
        return self.sections_dir / f"{filename}.tex"

    def read_section(self, name: str) -> str:
        path = self.section_path(name)
        if not path.exists():
            raise FileNotFoundError(f"章节不存在：{path}")
        return path.read_text(encoding="utf-8")

    def add_section(self, name: str, content: str) -> Path:
        """写入 ``sections/<name>.tex`` 并确保 main.tex 里 ``\\input`` 了它。"""
        if not self.main_tex.exists():
            raise FileNotFoundError(f"不是论文工程（缺少 main.tex）：{self.dest}")
        path = self.section_path(name)
        self.sections_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        main = self._read(self.main_tex)
        reference = f"\\input{{sections/{path.stem}}}"
        if reference not in main:
            main = self._insert_before_bibliography(main, reference)
            self.main_tex.write_text(main, encoding="utf-8")
        return path

    def _insert_before_bibliography(self, main: str, line: str) -> str:
        """把 ``line`` 插到参考文献区之前（保持 main.tex 结构可读）。"""
        markers = [
            "% -----------------------------------------------------------------------------\n% References",
            "\\bibliography{",
            "\\begin{thebibliography}",
        ]
        for marker in markers:
            index = main.find(marker)
            if index == -1:
                continue
            prefix = main[:index]
            if not prefix.endswith("\n"):
                prefix += "\n"
            suffix = main[index:]
            return f"{prefix}{line}\n\n{suffix}"
        return main.rstrip() + "\n\n" + line + "\n"

    def append_to_section(self, name: str, content: str) -> Path:
        """在章节末尾追加内容（不存在则新建）。"""
        path = self.section_path(name)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        return self.add_section(name, existing + separator + content)

    def add_bibtex(self, entries: str) -> Path:
        from sciforge.writing.citations import add_bibtex

        return add_bibtex(entries, self.bib_path)

    # ---- 统计 ----
    def stats(self) -> dict[str, Any]:
        files = self._tex_files()
        words: dict[str, int] = {}
        references = 0
        figures = 0
        tables = 0
        todos = 0
        for path, text in files:
            relative = str(path.relative_to(self.dest))
            words[relative] = _word_count(text)
            references += len(_CITE_RE.findall(text))
            figures += len(_INCLUDEGRAPHICS_RE.findall(text))
            tables += len(re.findall(r"\\begin\{table", text))
            todos += sum(1 for line in text.splitlines() if _PLACEHOLDER_RE.search(line))
        return {
            "words": words,
            "total_words": sum(words.values()),
            "citations": references,
            "figures": figures,
            "tables": tables,
            "todos": todos,
            "sections": self.list_sections(),
            "venue": self.venue,
        }

    # ---- 检查 ----
    def check(self) -> list[dict[str, Any]]:
        """静态检查论文工程，返回 issue 列表（severity: error|warning|info）。"""
        issues: list[dict[str, Any]] = []
        if not self.main_tex.exists():
            return [
                _issue(
                    "error",
                    "missing_main",
                    f"缺少 main.tex：{self.dest}。请先运行 create_paper_project 或检查路径。",
                    file=MAIN_TEX,
                )
            ]
        tex_files = self._tex_files()
        labels: dict[str, str] = {}
        refs: list[tuple[str, str, int]] = []
        cites: list[tuple[str, str, int]] = []
        includegraphics: list[tuple[str, str, int]] = []
        inputs: list[tuple[str, str, int]] = []

        for path, text in tex_files:
            relative = str(path.relative_to(self.dest))
            for line_number, line in enumerate(text.splitlines(), start=1):
                if line.lstrip().startswith("%"):
                    continue
                placeholder = _PLACEHOLDER_RE.search(line)
                if placeholder:
                    issues.append(
                        _issue(
                            "warning",
                            "placeholder",
                            f"存在占位符“{placeholder.group(0)}”：{line.strip()[:80]}",
                            file=relative,
                            line=line_number,
                        )
                    )
                for match in _LABEL_RE.finditer(line):
                    label = match.group(1).strip()
                    if label in labels:
                        issues.append(
                            _issue(
                                "warning",
                                "duplicate_label",
                                f"重复的 label：{label}",
                                file=relative,
                                line=line_number,
                            )
                        )
                    else:
                        labels[label] = relative
                for match in _REF_RE.finditer(line):
                    refs.append((match.group(1).strip(), relative, line_number))
                for match in _CITE_RE.finditer(line):
                    for key in re.split(r"\s*,\s*", match.group(1).strip()):
                        if key:
                            cites.append((key, relative, line_number))
                for match in _INCLUDEGRAPHICS_RE.finditer(line):
                    includegraphics.append((match.group(1).strip(), relative, line_number))
                for match in _INPUT_RE.finditer(line):
                    inputs.append((match.group(1).strip(), relative, line_number))

        # 未定义 \ref
        for ref, relative, line_number in refs:
            if ref not in labels:
                issues.append(
                    _issue(
                        "error",
                        "undefined_reference",
                        f"未定义的引用 \\ref{{{ref}}}（没有对应的 \\label）",
                        file=relative,
                        line=line_number,
                    )
                )
        # 引用了不存在的章节/表格文件
        for target, relative, line_number in inputs:
            candidate = self._resolve_tex_target(target)
            if candidate is None:
                issues.append(
                    _issue(
                        "error",
                        "missing_input",
                        f"\\input/\\include 的文件不存在：{target}",
                        file=relative,
                        line=line_number,
                    )
                )
        # 缺失的图
        for target, relative, line_number in includegraphics:
            resolved = self._resolve_graphic(target)
            if resolved is None:
                issues.append(
                    _issue(
                        "error",
                        "missing_figure",
                        f"缺少图片文件：{target}（请先生成 figure 或修正路径）",
                        file=relative,
                        line=line_number,
                    )
                )
            elif resolved.suffix.lower() == ".svg":
                issues.append(
                    _issue(
                        "warning",
                        "svg_figure",
                        f"图片 {target} 是 SVG，部分 LaTeX 引擎不支持；建议导出 PDF",
                        file=relative,
                        line=line_number,
                    )
                )
        # 未定义 \cite
        bib_keys = self._bib_keys()
        if not bib_keys:
            issues.append(
                _issue(
                    "error",
                    "missing_bibliography",
                    "refs.bib 不存在或没有任何 BibTeX 条目",
                    file="refs.bib",
                )
            )
        for key, relative, line_number in cites:
            if key and key not in bib_keys:
                issues.append(
                    _issue(
                        "error",
                        "undefined_citation",
                        f"未定义引用：\\cite{{{key}}} 不在 refs.bib 中",
                        file=relative,
                        line=line_number,
                    )
                )
        # 摘要
        main_text = self._read(self.main_tex)
        abstract_match = _ABSTRACT_RE.search(main_text)
        if not abstract_match:
            issues.append(_issue("error", "missing_abstract", "main.tex 缺少 abstract 环境"))
        else:
            words = _word_count(abstract_match.group(1))
            if words > 250:
                issues.append(
                    _issue(
                        "warning",
                        "abstract_too_long",
                        f"摘要 {words} 词，超过 250 词上限（多数期刊/会议限制 250 词）",
                        file=MAIN_TEX,
                    )
                )
            elif words < 40:
                issues.append(
                    _issue(
                        "warning",
                        "abstract_too_short",
                        f"摘要仅 {words} 词，建议写成 120–250 词的结构化摘要",
                        file=MAIN_TEX,
                    )
                )
        # 结构完整性
        if "\\begin{document}" not in main_text or "\\end{document}" not in main_text:
            issues.append(
                _issue("error", "broken_document", "main.tex 缺少 document 环境", file=MAIN_TEX)
            )
        if "\\bibliography{" not in main_text and "\\begin{thebibliography}" not in main_text:
            issues.append(
                _issue("error", "missing_bibliography", "main.tex 缺少参考文献命令", file=MAIN_TEX)
            )
        title_match = re.search(r"\\title\{([^}]*)\}", main_text)
        if not title_match or not title_match.group(1).strip():
            issues.append(_issue("warning", "missing_title", "\\title{} 为空", file=MAIN_TEX))
        elif _PLACEHOLDER_RE.search(title_match.group(1)):
            issues.append(_issue("warning", "placeholder_title", "标题仍是占位符", file=MAIN_TEX))
        # 章节
        section_titles = [
            title.strip().lower() for title in _SECTION_RE.findall(main_text + "\n" + "".join(
                self._read(path) for path in self._active_sections()
            ))
        ]
        for required in ("introduction", "method", "experiments", "conclusion"):
            if not any(required in title for title in section_titles):
                issues.append(
                    _issue(
                        "warning",
                        "missing_section",
                        f"缺少 {required.capitalize()} 章节（或章节标题不含该关键词）",
                        file=MAIN_TEX,
                    )
                )
        # venue 官方 sty
        style_file = VENUES.get(self.venue, {}).get("style_file")
        if style_file and not (self.dest / style_file).exists():
            issues.append(
                _issue(
                    "warning",
                    "missing_venue_style",
                    f"{VENUES[self.venue]['label']} 模板需要 {style_file}："
                    f"{VENUES[self.venue]['note']}",
                    file=MAIN_TEX,
                )
            )
        # 图表数量信息
        figure_count = sum(1 for path in self._active_sections() for _ in _INCLUDEGRAPHICS_RE.finditer(self._read(path)))
        figure_count += len(_INCLUDEGRAPHICS_RE.findall(main_text))
        if figure_count == 0:
            issues.append(
                _issue(
                    "info",
                    "no_figure",
                    "还没有插图：可用 `sciforge fig arch` / `sciforge fig plot` 生成后 \\includegraphics",
                )
            )
        # 排序：error → warning → info
        order = {"error": 0, "warning": 1, "info": 2}
        issues.sort(key=lambda item: (order.get(item["severity"], 3), item.get("file", ""), item.get("line", 0)))
        self._save_check_summary(issues)
        return issues

    # ---- 编译 ----
    def compile(self, *, timeout: float = 240.0) -> Path | None:
        """编译论文；成功返回 PDF 路径，失败返回 ``None``（原因写入 ``last_compile``）。"""
        if not self.main_tex.exists():
            raise FileNotFoundError(f"不是论文工程（缺少 main.tex）：{self.dest}")
        engine = self._pick_engine()
        if engine is None:
            self.last_compile = {
                "ok": False,
                "engine": None,
                "pdf": None,
                "message": (
                    "未检测到 LaTeX（latexmk / xelatex / pdflatex 都不在 PATH 中）。\n"
                    "安装建议（任选其一）：\n"
                    "  • TinyTeX（轻量，推荐）：curl -sL \"https://yihui.org/tinytex/install-bin-unix.sh\" | sh\n"
                    "  • macOS 完整发行版：brew install --cask mactex-no-gui\n"
                    "  • Ubuntu/Debian：sudo apt install texlive-full latexmk\n"
                    "安装后把 TeX bin 目录加进 PATH，再运行 `sciforge write compile`。"
                ),
                "log": "",
                "command": [],
            }
            return None
        commands = self._build_commands(engine)
        log_chunks: list[str] = []
        return_code = 0
        for command in commands:
            try:
                completed = subprocess.run(  # noqa: S603 - 本地已知命令
                    command,
                    cwd=str(self.dest),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except FileNotFoundError:
                self.last_compile = {
                    "ok": False,
                    "engine": engine,
                    "pdf": None,
                    "message": f"命令不存在：{command[0]}，请确认 TeX 安装是否完整。",
                    "log": "\n".join(log_chunks)[-8000:],
                    "command": command,
                }
                return None
            except subprocess.TimeoutExpired:
                self.last_compile = {
                    "ok": False,
                    "engine": engine,
                    "pdf": None,
                    "message": f"编译超时（>{timeout:.0f}s）。可先修复 check() 报错，或增大 timeout。",
                    "log": "\n".join(log_chunks)[-8000:],
                    "command": command,
                }
                return None
            log_chunks.append(f"$ {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")
            return_code = completed.returncode
            if return_code != 0:
                break
        pdf = self.dest / "main.pdf"
        ok = return_code == 0 and pdf.exists()
        log = "\n".join(log_chunks)[-12000:]
        message = "编译成功" if ok else self._explain_compile_failure(log, return_code)
        self.last_compile = {
            "ok": ok,
            "engine": engine,
            "pdf": str(pdf) if ok else None,
            "message": message,
            "log": log,
            "command": commands[0] if commands else [],
        }
        return pdf if ok else None

    # ---- 内部工具 ----
    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _tex_files(self) -> list[tuple[Path, str]]:
        files: list[tuple[Path, str]] = []
        candidates = [self.main_tex]
        candidates += sorted(self.sections_dir.glob("*.tex")) if self.sections_dir.exists() else []
        candidates += sorted(self.tables_dir.glob("*.tex")) if self.tables_dir.exists() else []
        candidates += sorted(self.dest.glob("*.tex"))
        seen: set[Path] = set()
        for path in candidates:
            if path in seen or not path.exists():
                continue
            seen.add(path)
            files.append((path, self._read(path)))
        return files

    def _active_sections(self) -> list[Path]:
        main = self._read(self.main_tex)
        paths: list[Path] = []
        for match in _INPUT_RE.finditer(main):
            resolved = self._resolve_tex_target(match.group(1))
            if resolved is not None:
                paths.append(resolved)
        return paths

    def _resolve_tex_target(self, target: str) -> Path | None:
        raw = target.strip()
        candidates = [self.dest / raw]
        if not raw.endswith(".tex"):
            candidates.append(self.dest / f"{raw}.tex")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _resolve_graphic(self, target: str) -> Path | None:
        raw = target.strip()
        candidates = [self.dest / raw]
        base = self.dest / raw
        if not base.suffix:
            for suffix in (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps"):
                candidates.append(base.with_suffix(suffix))
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _bib_keys(self) -> set[str]:
        keys: set[str] = set()
        for path in [self.bib_path, *self.dest.glob("*.bib")]:
            if not path.exists():
                continue
            keys.update(match.group(1) for match in _BIB_ENTRY_RE.finditer(self._read(path)))
        return keys

    def _save_check_summary(self, issues: list[dict[str, Any]]) -> None:
        try:
            summary = {
                "checked_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "n_errors": sum(1 for issue in issues if issue["severity"] == "error"),
                "n_warnings": sum(1 for issue in issues if issue["severity"] == "warning"),
                "issues": issues,
            }
            (self.dest / "check-report.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _pick_engine(self) -> str | None:
        for name in ("latexmk", "xelatex", "pdflatex"):
            if shutil.which(name):
                return name
        return None

    def _build_commands(self, engine: str) -> list[list[str]]:
        if engine == "latexmk":
            if shutil.which("xelatex"):
                return [["latexmk", "-xelatex", "-interaction=nonstopmode", "-halt-on-error", MAIN_TEX]]
            return [["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", MAIN_TEX]]
        compiler = engine
        commands = [
            [compiler, "-interaction=nonstopmode", "-halt-on-error", MAIN_TEX],
            ["bibtex", "main"],
            [compiler, "-interaction=nonstopmode", "-halt-on-error", MAIN_TEX],
            [compiler, "-interaction=nonstopmode", "-halt-on-error", MAIN_TEX],
        ]
        return commands

    def _explain_compile_failure(self, log: str, return_code: int) -> str:
        lines = [f"编译失败（返回码 {return_code}）。"]
        missing = re.findall(r"File `([^']+)' not found", log)
        if missing:
            lines.append(f"缺少宏包/样式文件：{', '.join(sorted(set(missing)))}")
            style_file = VENUES.get(self.venue, {}).get("style_file")
            if style_file and style_file in missing:
                lines.append(
                    f"当前是 {VENUES[self.venue]['label']} 模板：{VENUES[self.venue]['note']} "
                    "也可以用 `--venue generic` 生成不依赖官方 sty 的工程。"
                )
            else:
                lines.append("安装对应宏包（TeX Live: tlmgr install <package>），或先用 generic 模板。")
        errors = [line.strip() for line in log.splitlines() if line.startswith("!")][:6]
        if errors:
            lines.append("首个错误：\n" + "\n".join(errors))
        undefined = re.findall(r"Reference `([^']+)' on page", log) + re.findall(
            r"Citation `([^']+)' on page", log
        )
        if undefined:
            lines.append(f"未解析的引用：{', '.join(sorted(set(undefined)))}（检查 refs.bib / \\label）")
        lines.append("完整日志见 last_compile['log']；先跑 `sciforge write check` 可定位静态问题。")
        return "\n".join(lines)


__all__ = [
    "DEFAULT_SECTIONS",
    "META_FILE",
    "TEMPLATES_DIR",
    "VENUES",
    "PaperProject",
    "create_paper_project",
    "tex_escape_text",
]
