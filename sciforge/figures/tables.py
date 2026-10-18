"""论文表格生成：booktabs 三线表（LaTeX）与 Markdown。

功能：
- ``\\toprule / \\midrule / \\bottomrule`` 三线表，最佳结果加粗、次佳下划线；
- 多数据集列分组：``\\multicolumn`` 表头 + ``\\cmidrule(lr){a-b}``；
- caption / label / 列对齐 / 行分组（``\\midrule``）/ 脚注；
- 同时输出 Markdown 表格，便于在笔记/README 中展示。

spec 示例::

    caption: Main results on ImageNet and COCO.
    label: tab:main
    columns: [Method, Top-1, Top-5, AP, AP50]
    column_groups:
      - {title: ImageNet, columns: [1, 2]}
      - {title: COCO, columns: [3, 4]}
    metric_columns: [1, 2, 3, 4]
    rows:
      - [Baseline, 75.4, 92.1, 39.8, 61.2]
      - [Ours, 78.2, 93.4, 41.0, 63.7]
    note: Best in bold, second underlined.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# 常见 unicode 字符 → LaTeX 数学符号，避免在不同 TeX 引擎下编译失败
_UNICODE_MAP = {
    "±": r"$\pm$",
    "×": r"$\times$",
    "≤": r"$\leq$",
    "≥": r"$\geq$",
    "≈": r"$\approx$",
    "→": r"$\rightarrow$",
    "←": r"$\leftarrow$",
    "−": r"$-$",
    "∞": r"$\infty$",
    "α": r"$\alpha$",
    "β": r"$\beta$",
    "γ": r"$\gamma$",
    "δ": r"$\delta$",
    "λ": r"$\lambda$",
    "μ": r"$\mu$",
    "σ": r"$\sigma$",
    "τ": r"$\tau$",
    "θ": r"$\theta$",
    "Δ": r"$\Delta$",
    "·": r"$\cdot$",
    "…": r"\ldots{}",
    "–": "--",
    "—": "---",
}

_LATEX_SPECIALS = {
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


@dataclass
class TableRenderResult:
    """表格渲染结果（文本 + 可写入的文件内容）。"""

    latex: str
    markdown: str
    columns: list[str]
    n_rows: int
    best: dict[int, list[str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.best is None:
            self.best = {}


def tex_escape(text: Any) -> str:
    """转义 LaTeX 特殊字符（已是数学片段的 ``$...$`` 会被保留）。"""
    value = str(text)
    pieces: list[str] = []
    for chunk in re.split(r"(\$[^$]*\$)", value):
        if chunk.startswith("$") and chunk.endswith("$") and len(chunk) > 1:
            pieces.append(chunk)
            continue
        escaped = "".join(_LATEX_SPECIALS.get(char, char) for char in chunk)
        pieces.append(escaped)
    return "".join(pieces)


def smart_text(text: Any) -> str:
    """先转义 LaTeX 特殊字符，再把常见 unicode 符号换成 TeX 命令。"""
    escaped = tex_escape(text)
    for source, target in _UNICODE_MAP.items():
        if source in escaped:
            escaped = escaped.replace(source, target)
    return escaped


def _parse_number(value: Any) -> float | None:
    """从单元格里提取用于比较的数值（支持 ``78.2 ± 0.3``、``78.2*``）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    match = re.match(r"^[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _format_cell(value: Any, digits: int | None) -> str:
    if isinstance(value, float):
        if digits is None:
            text = f"{value:g}"
        else:
            text = f"{value:.{digits}f}"
        return text
    return str(value)


def _raw_cell_text(cell: Any) -> str:
    """高亮比较用：单元格的原始文本（不做 TeX 转义）。"""
    if isinstance(cell, dict):
        return str(cell.get("value", cell.get("text", "")))
    return str(cell)


def _cell_tex(cell: Any, *, raw: bool = False) -> str:
    if isinstance(cell, dict):
        raw = bool(cell.get("raw", raw))
        cell = cell.get("value", cell.get("text", ""))
    text = str(cell)
    return text if raw else smart_text(text)


def _column_meta(spec: dict[str, Any]) -> list[dict[str, Any]]:
    columns = spec.get("columns")
    if not columns:
        raise ValueError("表格 spec 缺少 columns")
    meta: list[dict[str, Any]] = []
    for index, column in enumerate(columns):
        if isinstance(column, dict):
            meta.append(
                {
                    "name": str(column.get("name") or column.get("title") or f"col{index}"),
                    "align": str(column.get("align") or ""),
                    "raw": bool(column.get("raw", False)),
                }
            )
        else:
            meta.append({"name": str(column), "align": "", "raw": False})
    return meta


def _resolve_align(spec: dict[str, Any], columns: list[dict[str, Any]]) -> str:
    align = spec.get("align")
    if align:
        return str(align)
    default_label = str(spec.get("label_align") or "l")
    default_metric = str(spec.get("metric_align") or "c")
    metric_columns = set(_metric_columns(spec, columns))
    chars = []
    for index, column in enumerate(columns):
        chars.append(column["align"] or (default_metric if index in metric_columns else default_label))
    return "".join(chars)


def _metric_columns(spec: dict[str, Any], columns: list[dict[str, Any]]) -> list[int]:
    explicit = spec.get("metric_columns")
    if explicit is not None:
        return [int(index) for index in explicit if 0 <= int(index) < len(columns)]
    # 默认：除首列外、且表头不是文本描述列，全部作为指标列
    return list(range(1, len(columns)))


def _direction(spec: dict[str, Any], column_index: int) -> bool:
    """True 表示越大越好。"""
    higher = spec.get("higher_is_better", True)
    if isinstance(higher, dict):
        return bool(higher.get(column_index, higher.get(str(column_index), True)))
    if isinstance(higher, list):
        return bool(higher[column_index]) if column_index < len(higher) else True
    return bool(higher)


def _normalize_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """把 rows 规范成 ``[{cells, rule}]``，支持行分组与分隔线。"""
    rows_raw = spec.get("rows")
    if rows_raw is None:
        raise ValueError("表格 spec 缺少 rows")
    normalized: list[dict[str, Any]] = []
    for row in rows_raw:
        if isinstance(row, dict):
            if "rule" in row and "cells" not in row:
                normalized.append({"cells": [], "rule": str(row["rule"])})
                continue
            cells = list(row.get("cells") or [])
            normalized.append({"cells": cells, "rule": row.get("rule"), "raw": bool(row.get("raw", False))})
        elif isinstance(row, str) and row.strip().lower() in (
            "midrule",
            "addlinespace",
            "cmidrule",
            "toprule",
        ):
            normalized.append({"cells": [], "rule": row.strip().lower()})
        else:
            normalized.append({"cells": list(row), "rule": None})
    # row_groups：在每组之后插入 \midrule
    groups = spec.get("row_groups")
    if groups and not any(item.get("rule") for item in normalized):
        grouped: list[dict[str, Any]] = []
        cursor = 0
        for group in groups:
            count = int(group.get("rows") or group.get("count") or 0)
            label = group.get("label")
            if label:
                grouped.append({"cells": [], "rule": "group", "label": str(label)})
            grouped.extend(normalized[cursor : cursor + count])
            cursor += count
            grouped.append({"cells": [], "rule": "midrule"})
        grouped.extend(normalized[cursor:])
        while grouped and grouped[-1].get("rule") == "midrule":
            grouped.pop()
        normalized = grouped
    return normalized


def _compute_highlights(
    spec: dict[str, Any],
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[int, list[str]]:
    """计算每列 best / second 的原始单元格文本。"""
    if spec.get("highlight") is False:
        return {}
    metric_columns = _metric_columns(spec, columns)
    highlights: dict[int, list[str]] = {}
    for column_index in metric_columns:
        values: list[tuple[float, str]] = []
        for row in rows:
            cells = row.get("cells") or []
            if column_index >= len(cells) or row.get("rule"):
                continue
            cell = cells[column_index]
            value = _parse_number(cell.get("value") if isinstance(cell, dict) else cell)
            if value is None:
                continue
            values.append((value, _raw_cell_text(cell)))
        if not values:
            continue
        direction = 1.0 if _direction(spec, column_index) else -1.0
        best_value = max(values, key=lambda item: item[0] * direction)[0]
        best = [text for value, text in values if value == best_value]
        remaining = [item for item in values if item[0] != best_value]
        second: list[str] = []
        if remaining:
            second_value = max(remaining, key=lambda item: item[0] * direction)[0]
            second = [text for value, text in remaining if value == second_value]
        highlights[column_index] = best + [f"__second__{text}" for text in second]
    return highlights


def _apply_highlights(
    spec: dict[str, Any],
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, list[str]]]:
    highlights = _compute_highlights(spec, columns, rows)
    best_formatter = str(spec.get("best_format") or r"\textbf")
    second_formatter = str(spec.get("second_format") or r"\underline")
    second_keys: dict[int, set[str]] = {}
    for column_index, marks in highlights.items():
        second_keys[column_index] = {
            item[len("__second__") :] for item in marks if item.startswith("__second__")
        }
    for row in rows:
        if row.get("rule"):
            continue
        cells = row.get("cells") or []
        for column_index, marks in highlights.items():
            if column_index >= len(cells):
                continue
            cell = cells[column_index]
            raw = isinstance(cell, dict) and bool(cell.get("raw"))
            key = _raw_cell_text(cell)
            text = _cell_tex(cell, raw=raw)
            best_texts = {item for item in marks if not item.startswith("__second__")}
            if key in best_texts:
                text = f"{best_formatter}{{{text}}}"
            elif key in second_keys.get(column_index, set()):
                text = f"{second_formatter}{{{text}}}"
            cells[column_index] = {"value": text, "raw": True}
        row["cells"] = cells
    return rows, highlights


def _render_header(
    spec: dict[str, Any],
    columns: list[dict[str, Any]],
    *,
    grouped: bool,
) -> list[str]:
    groups = spec.get("column_groups") or []
    if not groups:
        header = " & ".join(_cell_tex(column["name"], raw=column["raw"]) for column in columns) + r" \\"
        return [header]

    # 校验列分组连续
    spans: list[tuple[int, int, str]] = []
    in_group: set[int] = set()
    for group in groups:
        cols = [int(index) for index in group.get("columns") or []]
        if not cols:
            continue
        low, high = min(cols), max(cols)
        if cols != list(range(low, high + 1)):
            raise ValueError(f"列分组 {group.get('title')!r} 的 columns 必须是连续的：{cols}")
        if any(index in in_group for index in cols):
            raise ValueError(f"列分组 {group.get('title')!r} 与其他分组有重叠列")
        in_group.update(cols)
        spans.append((low, high, str(group.get("title") or "")))
    spans.sort()

    align = _resolve_align(spec, columns)
    top_cells: list[str] = []
    cursor = 0
    for low, high, title in spans:
        while cursor < low:
            if cursor == 0 and _is_label_column(spec):
                top_cells.append(f"\\multicolumn{{1}}{{{align[cursor]}}}{{{_cell_tex(columns[cursor]['name'])}}}")
            else:
                top_cells.append(f"\\multicolumn{{1}}{{{align[cursor]}}}{{}}")
            cursor += 1
        span_align = str(spec.get("group_align") or "c")
        top_cells.append(f"\\multicolumn{{{high - low + 1}}}{{{span_align}}}{{\\textbf{{{smart_text(title)}}}}}")
        cursor = high + 1
    while cursor < len(columns):
        top_cells.append(f"\\multicolumn{{1}}{{{align[cursor]}}}{{\\textbf{{{_cell_tex(columns[cursor]['name'])}}}}}")
        cursor += 1
    header_lines = [" & ".join(top_cells) + r" \\"]
    cmidrules = " ".join(f"\\cmidrule(lr){{{low + 1}-{high + 1}}}" for low, high, _ in spans)
    if cmidrules:
        header_lines.append(cmidrules)
    sub_cells: list[str] = []
    for index, column in enumerate(columns):
        if index in in_group:
            sub_cells.append(_cell_tex(column["name"], raw=column["raw"]))
        else:
            sub_cells.append("")
    if any(sub_cells):
        header_lines.append(" & ".join(sub_cells) + r" \\")
    del grouped
    return header_lines


def _is_label_column(spec: dict[str, Any]) -> bool:
    return str(spec.get("label_column") or "first") != "none"


def _render_body(
    spec: dict[str, Any],
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[str]:
    digits = spec.get("digits")
    digits_int = int(digits) if digits is not None else None
    lines: list[str] = []
    for row in rows:
        rule = row.get("rule")
        if rule:
            if rule == "midrule":
                lines.append(r"\midrule")
            elif rule == "group":
                if row.get("label"):
                    label = str(row["label"])
                    span = len(columns)
                    lines.append(
                        f"\\multicolumn{{{span}}}{{l}}{{\\textit{{{smart_text(label)}}}}} \\\\"
                    )
            elif rule == "addlinespace":
                lines.append(r"\addlinespace")
            elif rule == "cmidrule":
                lines.append(r"\cmidrule(lr){2-" + str(len(columns)) + "}")
            elif rule == "toprule":
                lines.append(r"\toprule")
            continue
        cells = row.get("cells") or []
        rendered: list[str] = []
        for index, column in enumerate(columns):
            cell = cells[index] if index < len(cells) else ""
            if isinstance(cell, dict):
                raw = bool(cell.get("raw"))
                value = cell.get("value", cell.get("text", ""))
            else:
                raw = bool(column.get("raw"))
                value = cell
            if index in _metric_columns(spec, columns) and isinstance(value, float):
                value = _format_cell(value, digits_int)
            rendered.append(_cell_tex(value, raw=raw))
        lines.append(" & ".join(rendered) + r" \\")
    return lines


def render_latex_table(spec: dict[str, Any], *, standalone: bool | None = None) -> str:
    """渲染为 booktabs 三线表 LaTeX 字符串。

    ``standalone`` 为 True 时输出完整 ``table`` 浮动体（默认），
    False 时只输出 ``tabular``。
    """
    if not isinstance(spec, dict):
        raise TypeError("spec 必须是 dict")
    columns = _column_meta(spec)
    rows = _normalize_rows(spec)
    rows, highlights = _apply_highlights(spec, columns, rows)
    align = _resolve_align(spec, columns)
    grouped = bool(spec.get("column_groups"))
    header = _render_header(spec, columns, grouped=grouped)
    body = _render_body(spec, columns, rows)

    tabular_lines = [
        f"\\begin{{tabular}}{{{align}}}",
        r"\toprule",
        *header,
        r"\midrule",
        *body,
        r"\bottomrule",
        r"\end{tabular}",
    ]
    tabular = "\n".join(tabular_lines)

    if standalone is None:
        standalone = bool(spec.get("float", True))
    if not standalone:
        return tabular

    position = str(spec.get("position") or "t")
    size = str(spec.get("font_size") or r"\small")
    caption = smart_text(spec.get("caption") or "")
    label = str(spec.get("label") or "")
    note = smart_text(spec.get("note") or "")

    lines = [
        r"% Requires \usepackage{booktabs}",
        f"\\begin{{table}}[{position}]",
        r"\centering",
        size,
        f"\\caption{{{caption}}}" if caption else r"% \caption{TODO: add caption}",
        f"\\label{{{label}}}" if label else r"% \label{tab:todo}",
        tabular,
        f"\\par\\smallskip\\footnotesize {note}" if note else "",
        r"\end{table}",
    ]
    return "\n".join(line for line in lines if line != "")


def _markdown_align(align: str) -> str:
    mapping = {"l": ":---", "c": ":---:", "r": "---:"}
    return " ".join(mapping.get(char, ":---") for char in align)


def render_markdown_table(spec: dict[str, Any]) -> str:
    """渲染为 GitHub 风格 Markdown 表格（best 加粗、second 斜体）。"""
    columns = _column_meta(spec)
    rows = _normalize_rows(spec)
    rows, _highlights = _apply_highlights(spec, columns, rows)
    align = _resolve_align(spec, columns)

    header = "| " + " | ".join(column["name"] for column in columns) + " |"
    separator = "| " + " | ".join(_markdown_align(char) for char in align) + " |"
    lines = [header, separator]
    for row in rows:
        if row.get("rule"):
            continue
        cells = row.get("cells") or []
        rendered = []
        for index in range(len(columns)):
            cell = cells[index] if index < len(cells) else ""
            if isinstance(cell, dict):
                value = cell.get("value", "")
            else:
                value = cell
            text = str(value)
            # LaTeX 高亮 → Markdown 强调
            text = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", text)
            text = re.sub(r"\\underline\{([^{}]*)\}", r"_\1_", text)
            text = (
                text.replace(r"\_", "_")
                .replace(r"\&", "&")
                .replace(r"\%", "%")
                .replace(r"\#", "#")
            )
            rendered.append(text.replace("$", ""))
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def render_table(spec: dict[str, Any], *, fmt: str = "latex") -> str:
    """按 ``fmt``（latex | markdown）渲染表格。"""
    if fmt in ("latex", "tex", "booktabs"):
        return render_latex_table(spec)
    if fmt in ("markdown", "md"):
        return render_markdown_table(spec)
    raise ValueError(f"不支持的表格格式：{fmt!r}（可用 latex / markdown）")


def load_spec(path: str | Path) -> dict[str, Any]:
    spec_path = Path(path).expanduser()
    if not spec_path.exists():
        raise FileNotFoundError(f"spec 文件不存在：{spec_path}")
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"spec 必须是映射（dict）：{spec_path}")
    return data


def save_spec(spec: dict[str, Any], path: str | Path) -> Path:
    spec_path = Path(path).expanduser()
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return spec_path


# ---------------------------------------------------------------------------
# 内置示例
# ---------------------------------------------------------------------------


def main_results_spec() -> dict[str, Any]:
    """内置示例：跨数据集主结果表。"""
    return {
        "caption": "Main results on ImageNet and COCO. Best in bold, second underlined.",
        "label": "tab:main",
        "columns": ["Method", "Top-1", "Top-5", "AP", "AP$_{50}$"],
        "column_groups": [
            {"title": "ImageNet", "columns": [1, 2]},
            {"title": "COCO", "columns": [3, 4]},
        ],
        "metric_columns": [1, 2, 3, 4],
        "higher_is_better": {1: True, 2: True, 3: True, 4: True},
        "rows": [
            ["ResNet-50", 76.1, 92.9, "—", "—"],
            ["ViT-B/16", 77.9, 93.8, 39.4, 60.1],
            ["Ours", 79.2, 94.1, 41.0, 63.2],
            ["Ours + router", 79.8, 94.4, 41.3, 63.8],
        ],
        "note": "All models use 224 resolution. Best in bold, second underlined.",
    }


def ablation_spec() -> dict[str, Any]:
    """内置示例：消融实验表（含行分组）。"""
    return {
        "caption": "Ablation study on the routing and consistency objectives.",
        "label": "tab:ablation",
        "columns": ["Variant", "Worst-group Acc.", "Avg. Acc.", "Params (M)"],
        "metric_columns": [1, 2, 3],
        "higher_is_better": {1: True, 2: True, 3: False},
        "rows": [
            ["Baseline", 66.0, 78.4, 86.0],
            ["+ router", 71.6, 79.2, 87.8],
            ["+ consistency", 69.3, 78.9, 87.2],
            ["Ours", 74.1, 79.6, 87.8],
        ],
        "row_groups": [{"label": "ImageNet-LT", "rows": 4}],
        "note": "Params counted for the vision backbone only.",
    }


def write_builtin_templates(dest: str | Path) -> list[Path]:
    out_dir = Path(dest).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        save_spec(main_results_spec(), out_dir / "table_main_results.yaml"),
        save_spec(ablation_spec(), out_dir / "table_ablation.yaml"),
    ]


__all__ = [
    "TableRenderResult",
    "tex_escape",
    "smart_text",
    "render_latex_table",
    "render_markdown_table",
    "render_table",
    "load_spec",
    "save_spec",
    "main_results_spec",
    "ablation_spec",
    "write_builtin_templates",
]
