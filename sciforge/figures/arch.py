"""声明式模型架构图渲染器（SVG 优先，可转 PDF/PNG）。

设计目标：生成**可以直接贴进论文初稿**的模型结构图，而不是示意草图。

- 纯白背景、无阴影/渐变；统一 8pt 正文（子标签 7pt / 注释 6.5pt），
  线宽 0.9pt（≈ 1.2px @96dpi），圆角模块框，细箭头；
- Okabe-Ito 色盲友好配色，关键模块用主色（#0072B2）强调；
- 严格网格对齐；分组区域浅色底 + 虚线边框，组名居中压在边框上；
- 支持横向 pipeline 与纵向 block 两种走向（``direction: horizontal | vertical``）；
- ``kind=tensor`` 用扁条表示张量形状，``kind=note`` 用小字注释；
- 输出 ``<out>.svg``，并按需转换 ``.pdf`` / ``.png``；缺少 cairosvg 时
  依次尝试 ``rsvg-convert`` / ``inkscape`` / macOS 的 ``sips`` / ``qlmanage``，
  全部不可用则只产出 SVG，并在返回结果里写明降级原因与安装建议。

spec 示例（YAML）::

    title: Transformer Block
    direction: vertical          # horizontal(横向 pipeline) | vertical(纵向 block)
    width: 340                   # 画布最大宽度(pt)；>900 时视为像素
    nodes:
      - {id: x, label: x, sub: "(B, N, D)", kind: data, row: 0}
      - {id: ln1, label: LayerNorm, kind: module, row: 1, group: attn}
      - {id: mhsa, label: Multi-Head Self-Attention, kind: module, row: 2, group: attn}
      - {id: add1, label: "⊕", kind: op, row: 3, group: attn}
      - {id: tip, label: "N=seq len", kind: note, row: 6}
    edges:
      - {from: x, to: ln1}
      - {from: x, to: add1, style: skip, label: residual, offset: 26}
    groups:
      - {id: attn, label: Attention sublayer, row: 1, col: 0}

坐标语义：``groups[*].row/col`` 是分组在网格中的原点；组内节点的 ``row/col``
是相对原点的局部坐标。缺省坐标时按 ``direction`` 自动排布：
横向 = 组按列排、组内节点竖排；纵向 = 组按行排、组内节点横排。
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
import xml.sax.saxutils as _saxutils
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# 常量：配色 / 字号 / 几何
# ---------------------------------------------------------------------------

# Okabe-Ito 色盲友好配色（色盲读者也能区分）
OKABE_ITO = {
    "black": "#000000",
    "orange": "#E69F00",
    "sky_blue": "#56B4E9",
    "bluish_green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "reddish_purple": "#CC79A7",
}

INK = "#1B1B1B"            # 主文字
INK_SOFT = "#444C56"       # 次级文字
MUTED = "#6B7280"          # 注释
MAIN = OKABE_ITO["blue"]   # 关键模块主色
MAIN_DARK = "#005A8E"
MAIN_TINT = "#E4EFF8"
GREEN = OKABE_ITO["bluish_green"]
GREEN_TINT = "#E3F2EB"
ORANGE = OKABE_ITO["vermillion"]
ORANGE_TINT = "#FBEDE4"
GRAY_TINT = "#E8EDF3"
GRAY_STROKE = "#7E8C9D"
PANEL_FILL = "#F6F8FA"
PANEL_STROKE = "#AEB9C6"
PANEL_LABEL = "#4A5563"

BASE_FONT = 8.0            # 正文统一字号（pt）
SUB_FONT = 7.0             # 子标签
NOTE_FONT = 6.5            # 注释
TENSOR_FONT = 7.0          # 张量条
TITLE_FONT = 9.5
LEGEND_FONT = 6.5
GROUP_FONT = 7.2
EDGE_LABEL_FONT = 6.5
EDGE_WIDTH = 0.9           # ≈1.2px @96dpi
NODE_STROKE = 0.9
PANEL_STROKE_W = 0.8
LINE_LABEL = 1.22          # 行高系数
COL_GAP = 26.0
ROW_GAP = 20.0
GROUP_BAND_GAP = 8.0
GROUP_PAD = 9.0
CANVAS_MARGIN = 12.0
TITLE_GAP = 7.0
LEGEND_GAP = 9.0
DEFAULT_MAX_WIDTH = 504.0  # 双栏论文 7in ≈ 504pt
MAX_CANVAS_WIDTH = 720.0
CORNER = 4.0

KINDS = ("tensor", "module", "op", "loss", "data", "note")
KIND_LEGEND = {
    "module": "Module",
    "tensor": "Tensor",
    "data": "Input / Output",
    "op": "Operation",
    "loss": "Loss",
    "note": "Note",
}
KIND_STYLE = {
    "module": {"fill": MAIN_TINT, "stroke": MAIN, "text": "#123A57", "sub": INK_SOFT, "rx": 5.0},
    "data": {"fill": GREEN_TINT, "stroke": GREEN, "text": "#0B4D3A", "sub": INK_SOFT, "rx": "capsule"},
    "loss": {"fill": ORANGE_TINT, "stroke": ORANGE, "text": "#6B2C0B", "sub": INK_SOFT, "rx": 5.0},
    "op": {"fill": "#FFFFFF", "stroke": "#4B5563", "text": INK, "sub": INK_SOFT, "rx": 5.0},
    "tensor": {"fill": GRAY_TINT, "stroke": GRAY_STROKE, "text": "#2E3A47", "sub": INK_SOFT, "rx": 3.0},
    "note": {"fill": "none", "stroke": "none", "text": MUTED, "sub": MUTED, "rx": 0.0},
}
ACCENT_STYLE = {
    "fill": MAIN,
    "stroke": MAIN_DARK,
    "text": "#FFFFFF",
    "sub": "#D6E8F5",
    "rx": 5.0,
}
GROUP_TINTS = {
    "blue": ("#EEF4F9", "#9BB4CB", "#3F6B8A"),
    "green": ("#EDF6F1", "#9CC3B2", "#2F6B56"),
    "orange": ("#FCF4EC", "#D3B49A", "#7A4A21"),
    "purple": ("#F7EEF4", "#C7AABF", "#6B4460"),
    "gray": (PANEL_FILL, PANEL_STROKE, PANEL_LABEL),
    "none": (PANEL_FILL, PANEL_STROKE, PANEL_LABEL),
}

# 各类节点的尺寸约束： (min_w, min_h, pad_x, pad_y, max_text_w)
KIND_BOX = {
    "module": (86.0, 28.0, 9.0, 7.0, 168.0),
    "data": (78.0, 28.0, 10.0, 7.0, 150.0),
    "loss": (76.0, 28.0, 9.0, 7.0, 150.0),
    "op": (40.0, 24.0, 8.0, 5.0, 120.0),
    "tensor": (64.0, 15.0, 8.0, 2.5, 150.0),
    "note": (20.0, 10.0, 2.0, 1.0, 170.0),
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _f(value: float) -> str:
    """紧凑数字（避免 12.300000000000004 这类浮点尾巴）。"""
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _esc(text: Any) -> str:
    return _saxutils.escape(str(text), {'"': "&quot;", "'": "&apos;"})


def _text_width(text: str, size: float, *, bold: bool = False) -> float:
    """粗略估算文本宽度（pt）。CJK 按 1em，西文按字宽系数累加。"""
    total = 0.0
    for char in str(text):
        code = ord(char)
        if code >= 0x2E80:  # CJK / 全角
            total += size * 1.0
        elif char in "il.,:;!|'` ":
            total += size * 0.3
        elif char in "mwMW@%&":
            total += size * 0.86
        elif char in "()[]{}<>/\\":
            total += size * 0.38
        elif char.isupper():
            total += size * 0.68
        elif char.isdigit():
            total += size * 0.56
        else:
            total += size * 0.53
    return total * (1.06 if bold else 1.0)


def _wrap_text(text: str, max_width: float, size: float) -> list[str]:
    """按估算宽度换行；保留显式 ``\\n``，长单词/CJK 按字符切。"""
    paragraphs = [p for p in str(text).split("\n")]
    lines: list[str] = []
    for paragraph in paragraphs:
        if not paragraph.strip():
            lines.append("")
            continue
        if _text_width(paragraph, size) <= max_width:
            lines.append(paragraph.strip())
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if _text_width(candidate, size) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        # 单个超长词：按字符硬切
        hard: list[str] = []
        for line in lines:
            if _text_width(line, size) <= max_width:
                hard.append(line)
                continue
            piece = ""
            for char in line:
                if _text_width(piece + char, size) > max_width and piece:
                    hard.append(piece)
                    piece = char
                else:
                    piece += char
            if piece:
                hard.append(piece)
        lines = hard
    return lines or [""]


def _line_height(size: float) -> float:
    return size * LINE_LABEL


def _baseline(center_y: float, size: float) -> float:
    """单行文本垂直居中时的基线 y。"""
    return center_y + size * 0.36


def _marker_id(color: str) -> str:
    return "arrow-" + re.sub(r"[^0-9a-zA-Z]", "", color)


def _sanitize_id(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_-]", "-", str(value))


@dataclass
class Rect:
    """轴对齐矩形（pt）。"""

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    def expand(self, pad: float) -> Rect:
        return Rect(self.x - pad, self.y - pad, self.w + 2 * pad, self.h + 2 * pad)

    def union(self, other: Rect | None) -> Rect:
        if other is None:
            return self
        x0 = min(self.x, other.x)
        y0 = min(self.y, other.y)
        x1 = max(self.right, other.right)
        y1 = max(self.bottom, other.bottom)
        return Rect(x0, y0, x1 - x0, y1 - y0)


# ---------------------------------------------------------------------------
# spec 读取 / 保存
# ---------------------------------------------------------------------------


def load_spec(path: str | Path) -> dict[str, Any]:
    """读取 YAML / JSON spec 文件。"""
    spec_path = Path(path).expanduser()
    if not spec_path.exists():
        raise FileNotFoundError(f"spec 文件不存在：{spec_path}")
    text = spec_path.read_text(encoding="utf-8")
    if spec_path.suffix.lower() == ".json":
        import json

        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"spec 必须是映射（dict）：{spec_path}")
    return data


def save_spec(spec: dict[str, Any], path: str | Path) -> Path:
    """把 spec 落盘为 YAML（与成品图同目录，保证可复现）。"""
    spec_path = Path(path).expanduser()
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return spec_path


# ---------------------------------------------------------------------------
# 布局
# ---------------------------------------------------------------------------


def _normalize_kind(kind: Any, warnings: list[str], node_id: str) -> str:
    value = str(kind or "module").strip().lower()
    if value in KINDS:
        return value
    warnings.append(f"节点 {node_id} 的 kind={kind!r} 不支持，已按 module 处理")
    return "module"


def _measure_node(node: dict[str, Any]) -> None:
    """计算节点的宽高与换行结果（写回 node 内部字段）。"""
    kind = node["kind"]
    min_w, min_h, pad_x, pad_y, max_text_w = KIND_BOX[kind]
    base_font = NOTE_FONT if kind == "note" else (TENSOR_FONT if kind == "tensor" else BASE_FONT)
    sub_font = NOTE_FONT if kind == "note" else SUB_FONT
    hint_w = node.get("hint_w")
    hint_h = node.get("hint_h")
    wrap_width = float(hint_w) - 2 * pad_x if hint_w else max_text_w
    wrap_width = max(36.0, wrap_width)
    label_lines = _wrap_text(node["label"], wrap_width, base_font)
    sub_lines = _wrap_text(node["sub"], wrap_width, sub_font) if node.get("sub") else []

    text_w = max((_text_width(line, base_font) for line in label_lines), default=0.0)
    if sub_lines:
        text_w = max(text_w, max(_text_width(line, sub_font) for line in sub_lines))
    block_h = len(label_lines) * _line_height(base_font)
    if sub_lines:
        block_h += 1.0 + len(sub_lines) * _line_height(sub_font)

    if kind == "op" and len(node["label"].strip()) <= 2:
        node["font"] = base_font = 9.5
        label_lines = [node["label"].strip()]
        text_w = _text_width(node["label"], base_font)
    width = max(min_w, text_w + 2 * pad_x, float(hint_w or 0))
    height = max(min_h, block_h + 2 * pad_y, float(hint_h or 0))
    if kind == "tensor":
        width = max(width, text_w + 2 * pad_x)
        height = max(15.0, block_h + 2 * pad_y)
    node.update(
        {
            "w": width,
            "h": height,
            "label_lines": label_lines,
            "sub_lines": sub_lines,
            "font": base_font,
            "sub_font": sub_font,
            "pad_x": pad_x,
            "pad_y": pad_y,
        }
    )


@dataclass
class _Layout:
    nodes: dict[str, dict[str, Any]]
    groups: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    legend: list[str]
    warnings: list[str] = field(default_factory=list)
    direction: str = "horizontal"


def _parse_edge_style(edge: dict[str, Any]) -> dict[str, Any]:
    style = str(edge.get("style") or "").strip().lower()
    tokens = set(re.split(r"[\s,|/+]+", style)) if style else set()
    dashed = bool(edge.get("dashed")) or bool(tokens & {"dashed", "dash", "dotted"})
    skip = bool(tokens & {"skip", "arc", "residual", "bypass"})
    straight = bool(tokens & {"line", "straight", "direct"})
    color = edge.get("color") or "#3B4652"
    color_map = {"ink": "#3B4652", "muted": "#8A95A3", "accent": MAIN, "main": MAIN}
    color = color_map.get(str(color).lower(), str(color))
    return {"dashed": dashed, "skip": skip, "straight": straight, "color": color}


def _place_cell(
    grid: dict[tuple[int, int], str],
    nid: str,
    cell: tuple[int, int],
    direction: str,
    warnings: list[str],
) -> tuple[int, int]:
    """占位：冲突时沿主方向顺延到下一个空网格。"""
    if cell not in grid:
        grid[cell] = nid
        return cell
    warnings.append(f"节点 {nid} 的网格位置 {cell} 已被占用，已自动顺延")
    row, col = cell
    limit = 0
    while (row, col) in grid and limit < 200:
        if direction == "horizontal":
            col += 1
        else:
            row += 1
        limit += 1
    grid[(row, col)] = nid
    return (row, col)


def _first_free_in_line(
    grid: dict[tuple[int, int], str],
    origin: tuple[int, int],
    direction: str,
) -> tuple[int, int]:
    row, col = origin
    if direction == "horizontal":
        while (row, col) in grid:
            col += 1
        return (row, col)
    while (row, col) in grid:
        row += 1
    return (row, col)


def _layout(spec: dict[str, Any]) -> _Layout:
    warnings: list[str] = []
    direction_raw = str(spec.get("direction") or "horizontal").strip().lower()
    if direction_raw.startswith(("v", "t", "b")):
        direction = "vertical"
    elif direction_raw.startswith("h"):
        direction = "horizontal"
    else:
        direction = "horizontal"
        warnings.append(f"direction={direction_raw!r} 无法识别，已按 horizontal 处理")

    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, raw in enumerate(spec.get("nodes") or []):
        if not isinstance(raw, dict) or not raw.get("id"):
            warnings.append(f"第 {index + 1} 个节点缺少 id，已跳过")
            continue
        nid = str(raw["id"])
        if nid in nodes:
            warnings.append(f"节点 id 重复：{nid}，后一个已跳过")
            continue
        kind = _normalize_kind(raw.get("kind"), warnings, nid)
        node: dict[str, Any] = {
            "id": nid,
            "label": str(raw.get("label", nid)),
            "sub": str(raw["sub"]) if raw.get("sub") is not None else "",
            "kind": kind,
            "group": str(raw["group"]) if raw.get("group") else None,
            "row": raw.get("row"),
            "col": raw.get("col"),
            "hint_w": raw.get("w"),
            "hint_h": raw.get("h"),
            "accent": bool(raw.get("accent") or raw.get("emphasis") or raw.get("key")),
            "dashed": bool(raw.get("dashed")),
            "shape": str(raw["shape"]).lower() if raw.get("shape") else None,
            "raw": raw,
        }
        _measure_node(node)
        nodes[nid] = node
        order.append(nid)

    if not nodes:
        raise ValueError("spec 中没有任何有效节点（nodes 为空）")

    groups: dict[str, dict[str, Any]] = {}
    group_order: list[str] = []
    for index, raw in enumerate(spec.get("groups") or []):
        if not isinstance(raw, dict):
            continue
        gid = str(raw.get("id") or f"group{index}")
        if gid in groups:
            warnings.append(f"分组 id 重复：{gid}，后一个已跳过")
            continue
        groups[gid] = {
            "id": gid,
            "label": str(raw.get("label") or ""),
            "row": raw.get("row"),
            "col": raw.get("col"),
            "span": raw.get("span"),
            "rowspan": raw.get("rowspan"),
            "color": str(raw.get("color") or "none").lower(),
            "raw": raw,
            "members": [],
            "origin": None,
        }
        group_order.append(gid)

    for nid in order:
        gid = nodes[nid]["group"]
        if not gid:
            continue
        if gid not in groups:
            warnings.append(f"节点 {nid} 引用了不存在的分组 {gid!r}，已按无分组处理")
            nodes[nid]["group"] = None
            continue
        groups[gid]["members"].append(nid)

    grid: dict[tuple[int, int], str] = {}

    def resolve_explicit(nid: str) -> tuple[int, int] | None:
        node = nodes[nid]
        row, col = node["row"], node["col"]
        group = groups.get(node["group"]) if node["group"] else None
        if row is None and col is None:
            return None
        if group is not None and group["origin"] is not None:
            local_row = int(row or 0)
            local_col = int(col or 0)
            return (group["origin"][0] + local_row, group["origin"][1] + local_col)
        return (int(row or 0), int(col or 0))

    def max_used() -> int:
        if not grid:
            return -1
        if direction == "horizontal":
            return max(col for _, col in grid)
        return max(row for row, _ in grid)

    # 1) 显式原点的分组：先确定原点并放置其显式节点
    explicit_origin = [
        gid
        for gid in group_order
        if groups[gid]["row"] is not None or groups[gid]["col"] is not None
    ]
    auto_origin = [gid for gid in group_order if gid not in explicit_origin]
    for gid in explicit_origin:
        group = groups[gid]
        group["origin"] = (int(group["row"] or 0), int(group["col"] or 0))
        for nid in group["members"]:
            cell = resolve_explicit(nid)
            if cell is not None:
                nodes[nid]["cell"] = _place_cell(grid, nid, cell, direction, warnings)

    # 2) 无原点的分组：沿主方向顺延分配原点
    for gid in auto_origin:
        group = groups[gid]
        if direction == "horizontal":
            group["origin"] = (0, max_used() + 1)
        else:
            group["origin"] = (max_used() + 1, 0)
        for nid in group["members"]:
            cell = resolve_explicit(nid)
            if cell is not None:
                nodes[nid]["cell"] = _place_cell(grid, nid, cell, direction, warnings)

    # 3) 无分组但显式定位的节点
    for nid in order:
        node = nodes[nid]
        if node.get("cell") is not None or node["group"]:
            continue
        if node["row"] is not None or node["col"] is not None:
            cell = (int(node["row"] or 0), int(node["col"] or 0))
            node["cell"] = _place_cell(grid, nid, cell, direction, warnings)

    # 4) 其余节点自动排布
    for nid in order:
        node = nodes[nid]
        if node.get("cell") is not None:
            continue
        group = groups.get(node["group"]) if node["group"] else None
        if group is not None and group["origin"] is not None:
            cell = _first_free_in_line(grid, group["origin"], direction)
        elif direction == "horizontal":
            cell = _first_free_in_line(grid, (0, max_used() + 1), "horizontal")
        else:
            cell = _first_free_in_line(grid, (max_used() + 1, 0), "vertical")
        node["cell"] = _place_cell(grid, nid, cell, direction, warnings)

    # 网格尺寸
    col_w: dict[int, float] = {}
    row_h: dict[int, float] = {}
    for node in nodes.values():
        row, col = node["cell"]
        row_h[row] = max(row_h.get(row, 0.0), node["h"])
        if node["kind"] == "note":
            continue  # 注释是浮动小字，不参与列宽计算
        col_w[col] = max(col_w.get(col, 0.0), node["w"])
    cols = sorted(col_w)
    rows = sorted(row_h)

    def _axis_groups(axis: str) -> dict[int, str | None]:
        mapping: dict[int, str | None] = {}
        for node in nodes.values():
            index = node["cell"][1] if axis == "col" else node["cell"][0]
            if index in mapping and mapping[index] is not None:
                continue
            mapping[index] = node["group"]
        return mapping

    col_group = _axis_groups("col")
    row_group = _axis_groups("row")
    col_x: dict[int, float] = {}
    x = 0.0
    previous: str | None = None
    for col in cols:
        group = col_group.get(col)
        if col_x and group is not None and previous is not None and group != previous:
            x += GROUP_BAND_GAP
        col_x[col] = x
        x += col_w[col] + COL_GAP
        if group is not None:
            previous = group
    body_w = x - COL_GAP
    row_y: dict[int, float] = {}
    y = 0.0
    previous = None
    for row in rows:
        group = row_group.get(row)
        if row_y and group is not None and previous is not None and group != previous:
            y += GROUP_BAND_GAP
        row_y[row] = y
        y += row_h[row] + ROW_GAP
        if group is not None:
            previous = group
    body_h = y - ROW_GAP

    for node in nodes.values():
        row, col = node["cell"]
        raw = node["raw"]
        span_all = node["kind"] == "note" and (
            str(raw.get("span") or raw.get("width_span") or "").lower() == "all"
            or bool(raw.get("full_width"))
        )
        if span_all:
            width = body_w
            node["w"] = width
            rect = Rect(0.0, row_y[row] + (row_h[row] - node["h"]) / 2.0, width, node["h"])
        else:
            rect = Rect(
                col_x[col] + (col_w[col] - node["w"]) / 2.0,
                row_y[row] + (row_h[row] - node["h"]) / 2.0,
                node["w"],
                node["h"],
            )
        node["rect"] = rect

    # 分组面板
    body_rect = Rect(0.0, 0.0, body_w, body_h)
    for gid in group_order:
        group = groups[gid]
        members = [nodes[nid] for nid in group["members"] if nodes[nid]["kind"] != "note"]
        if not members:
            groups[gid]["rect"] = None
            continue
        rect = members[0]["rect"]
        for node in members[1:]:
            rect = rect.union(node["rect"])
        rect = rect.expand(GROUP_PAD)
        span = group["span"]
        if span == "all":
            rect = Rect(body_rect.x - GROUP_PAD, rect.y, body_rect.w + 2 * GROUP_PAD, rect.h)
        elif isinstance(span, int) and span > 1 and group["origin"] is not None:
            last_col = group["origin"][1] + span - 1
            if last_col in col_x:
                rect = Rect(
                    rect.x,
                    rect.y,
                    col_x[last_col] + col_w[last_col] + GROUP_PAD - rect.x,
                    rect.h,
                )
        rowspan = group["rowspan"]
        if rowspan == "all":
            rect = Rect(rect.x, body_rect.y - GROUP_PAD, rect.w, body_rect.h + 2 * GROUP_PAD)
        elif isinstance(rowspan, int) and rowspan > 1 and group["origin"] is not None:
            last_row = group["origin"][0] + rowspan - 1
            if last_row in row_y:
                rect = Rect(
                    rect.x,
                    rect.y,
                    rect.w,
                    row_y[last_row] + row_h[last_row] + GROUP_PAD - rect.y,
                )
        groups[gid]["rect"] = rect

    # 图例条目：强调色优先，然后按出现过的 kind
    legend: list[str] = []
    if any(node["accent"] for node in nodes.values()):
        legend.append("accent")
    for node in nodes.values():
        kind = node["kind"]
        if kind not in legend:
            legend.append(kind)
    legend = [item for item in legend if item in KIND_LEGEND or item == "accent"]
    if len(legend) <= 1:
        legend = []  # 单一元素不值得画图例

    # 边：解析端点、计算路径（自动避让中间节点）
    edges: list[dict[str, Any]] = []
    for raw in spec.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        src_id = str(raw.get("from", raw.get("source", "")))
        dst_id = str(raw.get("to", raw.get("target", "")))
        if not src_id or not dst_id:
            warnings.append(f"边缺少 from/to，已跳过：{raw!r}")
            continue
        src_rect = _endpoint_rect(src_id, nodes, groups)
        dst_rect = _endpoint_rect(dst_id, nodes, groups)
        if src_rect is None or dst_rect is None:
            warnings.append(f"边 {src_id}→{dst_id} 的端点不存在，已跳过")
            continue
        if src_id == dst_id:
            warnings.append(f"忽略自环边：{src_id}")
            continue
        edge = {
            "from": src_id,
            "to": dst_id,
            "label": str(raw["label"]) if raw.get("label") is not None else "",
            "raw": raw,
            "style": _parse_edge_style(raw),
            "offset": float(raw.get("offset") or 22.0),
            "side": str(raw.get("side") or "auto").lower(),
        }
        endpoint_ids = {src_id, dst_id}
        obstacles = [
            nodes[nid]["rect"] for nid in nodes if nid not in endpoint_ids
        ]
        panels = [
            group["rect"]
            for group in groups.values()
            if group.get("rect") is not None
        ]
        _route_edge(edge, src_rect, dst_rect, obstacles, panels)
        edges.append(edge)

    _spread_endpoints(edges, nodes)

    return _Layout(
        nodes=nodes,
        groups=groups,
        edges=edges,
        legend=legend,
        warnings=warnings,
        direction=direction,
    )


def _anchor_side(point: tuple[float, float], rect: Rect) -> str | None:
    tol = 1.0
    if abs(point[0] - rect.x) <= tol:
        return "left"
    if abs(point[0] - rect.right) <= tol:
        return "right"
    if abs(point[1] - rect.y) <= tol:
        return "top"
    if abs(point[1] - rect.bottom) <= tol:
        return "bottom"
    return None


def _spread_endpoints(
    edges: list[dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
) -> None:
    """同一节点同一侧的多个箭头端点沿边错开，避免箭头重叠在一个点上。"""
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for edge in edges:
        for which in ("start", "end"):
            node_id = edge["from"] if which == "start" else edge["to"]
            if node_id not in nodes:
                continue
            points = edge["points"]
            if not points:
                continue
            point = points[0] if which == "start" else points[-1]
            side = _anchor_side(point, nodes[node_id]["rect"])
            if side:
                buckets[(node_id, side)].append((edge, which))
    spacing = 7.0
    for (_node_id, side), items in buckets.items():
        if len(items) < 2:
            continue
        horizontal_side = side in ("left", "right")

        def sort_key(
            item: tuple[dict[str, Any], str],
            *,
            horizontal: bool = horizontal_side,
        ) -> float:
            edge, which = item
            point = edge["points"][0] if which == "start" else edge["points"][-1]
            return point[1] if horizontal else point[0]

        items.sort(key=sort_key)
        for index, (edge, which) in enumerate(items):
            if len(items) > 4:
                offset = 0.0
            else:
                offset = (index - (len(items) - 1) / 2.0) * spacing
            points = edge["points"]
            indices = [0] if which == "start" else [len(points) - 1]
            adjacent = 1 if which == "start" else len(points) - 2
            if 0 <= adjacent < len(points):
                if horizontal_side and abs(points[adjacent][1] - points[indices[0]][1]) < 0.5:
                    indices.append(adjacent)
                if not horizontal_side and abs(points[adjacent][0] - points[indices[0]][0]) < 0.5:
                    indices.append(adjacent)
            for point_index in indices:
                x, y = points[point_index]
                points[point_index] = (x, y + offset) if horizontal_side else (x + offset, y)
            edge["path"] = _rounded_path(
                points, radius=5.0 if edge["style"]["skip"] else CORNER
            )
            edge["label_at"] = _point_at(points, 0.5)


def _endpoint_rect(
    ref: str,
    nodes: dict[str, dict[str, Any]],
    groups: dict[str, dict[str, Any]],
) -> Rect | None:
    if ref in nodes:
        return nodes[ref]["rect"]
    if ref in groups:
        return groups[ref].get("rect")
    return None


# ---------------------------------------------------------------------------
# 连线几何
# ---------------------------------------------------------------------------


def _rounded_path(points: list[tuple[float, float]], radius: float = CORNER) -> str:
    """把折线转成带圆角拐弯的 SVG path。"""
    if len(points) < 2:
        return ""
    if len(points) == 2:
        (x0, y0), (x1, y1) = points
        return f"M {_f(x0)} {_f(y0)} L {_f(x1)} {_f(y1)}"
    parts = [f"M {_f(points[0][0])} {_f(points[0][1])}"]
    for index in range(1, len(points) - 1):
        prev = points[index - 1]
        cur = points[index]
        nxt = points[index + 1]
        d1 = math.dist(prev, cur)
        d2 = math.dist(cur, nxt)
        if d1 < 1e-6 or d2 < 1e-6:
            continue
        r = min(radius, d1 / 2.0, d2 / 2.0)
        before = (
            cur[0] + (prev[0] - cur[0]) * r / d1,
            cur[1] + (prev[1] - cur[1]) * r / d1,
        )
        after = (
            cur[0] + (nxt[0] - cur[0]) * r / d2,
            cur[1] + (nxt[1] - cur[1]) * r / d2,
        )
        parts.append(f"L {_f(before[0])} {_f(before[1])}")
        parts.append(f"Q {_f(cur[0])} {_f(cur[1])} {_f(after[0])} {_f(after[1])}")
    parts.append(f"L {_f(points[-1][0])} {_f(points[-1][1])}")
    return " ".join(parts)


def _polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def _point_at(points: list[tuple[float, float]], t: float) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return points[0]
    total = _polyline_length(points)
    if total < 1e-6:
        return points[0]
    target = max(0.0, min(1.0, t)) * total
    walked = 0.0
    for i in range(len(points) - 1):
        seg = math.dist(points[i], points[i + 1])
        if walked + seg >= target:
            ratio = (target - walked) / seg if seg else 0.0
            return (
                points[i][0] + (points[i + 1][0] - points[i][0]) * ratio,
                points[i][1] + (points[i + 1][1] - points[i][1]) * ratio,
            )
        walked += seg
    return points[-1]


def _segment_hits_rect(
    a: tuple[float, float],
    b: tuple[float, float],
    rect: Rect,
    pad: float = 1.5,
) -> bool:
    """正交线段是否穿过矩形（用于避让节点）。"""
    left, right = rect.x - pad, rect.right + pad
    top, bottom = rect.y - pad, rect.bottom + pad
    if abs(a[1] - b[1]) < 1e-6:  # 水平段
        y = a[1]
        if not (top < y < bottom):
            return False
        x0, x1 = sorted((a[0], b[0]))
        return x1 > left and x0 < right
    if abs(a[0] - b[0]) < 1e-6:  # 垂直段
        x = a[0]
        if not (left < x < right):
            return False
        y0, y1 = sorted((a[1], b[1]))
        return y1 > top and y0 < bottom
    return False


def _path_hits_obstacles(
    points: list[tuple[float, float]],
    obstacles: list[Rect],
) -> bool:
    for index in range(len(points) - 1):
        for rect in obstacles:
            if _segment_hits_rect(points[index], points[index + 1], rect):
                return True
    return False


def _route_edge(
    edge: dict[str, Any],
    src: Rect,
    dst: Rect,
    obstacles: list[Rect] | None = None,
    panels: list[Rect] | None = None,
) -> None:
    """计算起止锚点与折线；结果写入 ``edge["points"]/["path"]/["label_at"]``。"""
    style = edge["style"]
    dx = dst.cx - src.cx
    dy = dst.cy - src.cy
    horizontal = abs(dx) >= abs(dy)
    obstacles = obstacles or []
    panels = panels or []

    if style["skip"]:
        side = edge["side"]
        if horizontal or side in ("top", "bottom"):
            above = side != "bottom"
            span_left, span_right = min(src.x, dst.x), max(src.right, dst.right)
            relevant = [p for p in panels if p.right > span_left and p.x < span_right]
            if above:
                y_channel = min([src.y, dst.y] + [p.y for p in relevant]) - edge["offset"]
            else:
                y_channel = max([src.bottom, dst.bottom] + [p.bottom for p in relevant]) + edge["offset"]
            start = (src.cx, src.y if above else src.bottom)
            end = (dst.cx, dst.y if above else dst.bottom)
            points = [start, (start[0], y_channel), (end[0], y_channel), end]
            label_x = (start[0] + end[0]) / 2.0
            edge["label_at"] = (label_x, y_channel - 5.0 if above else y_channel + 5.0)
        else:
            left = side == "left"
            span_top, span_bottom = min(src.y, dst.y), max(src.bottom, dst.bottom)
            relevant = [p for p in panels if p.bottom > span_top and p.y < span_bottom]
            if left:
                x_channel = min([src.x, dst.x] + [p.x for p in relevant]) - edge["offset"]
            else:
                x_channel = max([src.right, dst.right] + [p.right for p in relevant]) + edge["offset"]
            start = (src.x if left else src.right, src.cy)
            end = (dst.x if left else dst.right, dst.cy)
            points = [start, (x_channel, start[1]), (x_channel, end[1]), end]
            edge["label_at"] = (
                x_channel + (-5.0 if left else 5.0),
                (start[1] + end[1]) / 2.0,
            )
        edge["points"] = points
        edge["path"] = _rounded_path(points, radius=5.0)
        return

    if style["straight"]:
        if horizontal:
            start = (src.right if dx >= 0 else src.x, src.cy)
            end = (dst.x if dx >= 0 else dst.right, dst.cy)
        else:
            start = (src.cx, src.bottom if dy >= 0 else src.y)
            end = (dst.cx, dst.y if dy >= 0 else dst.bottom)
        points = [start, end]
        edge["points"] = points
        edge["path"] = _rounded_path(points)
        edge["label_at"] = _point_at(points, 0.5)
        return

    # 默认：正交折线（水平优先/垂直优先取决于相对位置）
    if horizontal:
        if dx >= 0:
            start, end = (src.right, src.cy), (dst.x, dst.cy)
        else:
            start, end = (src.x, src.cy), (dst.right, dst.cy)
        if abs(start[1] - end[1]) < 0.5:
            points = [start, end]
        else:
            mid_x = (start[0] + end[0]) / 2.0
            points = [start, (mid_x, start[1]), (mid_x, end[1]), end]
    else:
        if dy >= 0:
            start, end = (src.cx, src.bottom), (dst.cx, dst.y)
        else:
            start, end = (src.cx, src.y), (dst.cx, dst.bottom)
        if abs(start[0] - end[0]) < 0.5:
            points = [start, end]
        else:
            mid_y = (start[1] + end[1]) / 2.0
            points = [start, (start[0], mid_y), (end[0], mid_y), end]
    edge["points"] = points
    edge["path"] = _rounded_path(points)
    edge["label_at"] = _point_at(points, 0.5)

    # 避让：若直连路径穿过中间节点，则绕到节点外侧的“通道”
    if obstacles and _path_hits_obstacles(points, obstacles):
        blocked = [
            rect
            for rect in obstacles
            if any(
                _segment_hits_rect(points[i], points[i + 1], rect)
                for i in range(len(points) - 1)
            )
        ]
        if horizontal:
            span_left, span_right = min(src.x, dst.x), max(src.right, dst.right)
            relevant = [p for p in panels if p.right > span_left and p.x < span_right]
            y_channel = max(
                [src.bottom, dst.bottom]
                + [rect.bottom for rect in blocked]
                + [p.bottom for p in relevant]
            ) + edge["offset"]
            points = [
                (src.cx, src.bottom),
                (src.cx, y_channel),
                (dst.cx, y_channel),
                (dst.cx, dst.bottom),
            ]
            edge["label_at"] = ((src.cx + dst.cx) / 2.0, y_channel - 5.5)
        else:
            span_top, span_bottom = min(src.y, dst.y), max(src.bottom, dst.bottom)
            relevant = [p for p in panels if p.bottom > span_top and p.y < span_bottom]
            x_channel = max(
                [src.right, dst.right]
                + [rect.right for rect in blocked]
                + [p.right for p in relevant]
            ) + edge["offset"]
            points = [
                (src.right, src.cy),
                (x_channel, src.cy),
                (x_channel, dst.cy),
                (dst.right, dst.cy),
            ]
            edge["label_at"] = (x_channel + 5.5, (src.cy + dst.cy) / 2.0)
        edge["points"] = points
        edge["path"] = _rounded_path(points)
        edge["routed_around"] = True


# ---------------------------------------------------------------------------
# SVG 渲染
# ---------------------------------------------------------------------------


def _node_style(node: dict[str, Any]) -> dict[str, Any]:
    style = dict(ACCENT_STYLE if node["accent"] else KIND_STYLE[node["kind"]])
    shape = node["shape"]
    if shape in ("capsule", "pill"):
        style["rx"] = "capsule"
    elif shape in ("rect", "square"):
        style["rx"] = 2.0
    elif shape == "round":
        style["rx"] = "capsule"
    return style


def _node_anchor_points(node: dict[str, Any]) -> tuple[float, float]:
    rect = node["rect"]
    return (rect.cx, rect.cy)


def _render_node(node: dict[str, Any]) -> str:
    rect: Rect = node["rect"]
    style = _node_style(node)
    kind = node["kind"]
    shape = node["shape"] or ("diamond" if kind == "op" and node["label"].strip() in ("?", "◇") else "")

    if kind == "note":
        return _render_note(node, rect, style)

    stroke_dash = ' stroke-dasharray="3 2"' if node["dashed"] else ""
    stroke_w = NODE_STROKE
    common = (
        f'stroke="{style["stroke"]}" stroke-width="{_f(stroke_w)}"'
        f'{stroke_dash}'
    )
    parts: list[str] = []
    if shape == "diamond":
        cx, cy = rect.cx, rect.cy
        points = (
            f"{_f(cx)},{_f(rect.y)} {_f(rect.right)},{_f(cy)} "
            f"{_f(cx)},{_f(rect.bottom)} {_f(rect.x)},{_f(cy)}"
        )
        parts.append(f'<polygon points="{points}" fill="{style["fill"]}" {common}/>')
    elif shape == "circle":
        radius = min(rect.w, rect.h) / 2.0
        parts.append(
            f'<circle cx="{_f(rect.cx)}" cy="{_f(rect.cy)}" r="{_f(radius)}" '
            f'fill="{style["fill"]}" {common}/>'
        )
    else:
        rx = rect.h / 2.0 if style["rx"] == "capsule" else float(style["rx"])
        if kind == "tensor":
            rx = 3.0
        parts.append(
            f'<rect x="{_f(rect.x)}" y="{_f(rect.y)}" width="{_f(rect.w)}" '
            f'height="{_f(rect.h)}" rx="{_f(rx)}" fill="{style["fill"]}" {common}/>'
        )
        if kind == "tensor":
            # 张量条左侧留一条细色带，暗示“张量形状”而非普通模块
            parts.append(
                f'<rect x="{_f(rect.x)}" y="{_f(rect.y)}" width="2.6" '
                f'height="{_f(rect.h)}" rx="1.3" fill="{style["stroke"]}" opacity="0.75"/>'
            )

    # 文本
    label_lines: list[str] = node["label_lines"]
    sub_lines: list[str] = node["sub_lines"]
    font = node["font"]
    sub_font = node["sub_font"]
    lh = _line_height(font)
    sub_lh = _line_height(sub_font)
    block_h = len(label_lines) * lh
    if sub_lines:
        block_h += 1.0 + len(sub_lines) * sub_lh
    top = rect.cy - block_h / 2.0
    for index, line in enumerate(label_lines):
        if not line:
            continue
        y = top + index * lh + lh / 2.0 + font * 0.32
        weight = "600" if kind in ("module", "data", "loss", "op") else "normal"
        parts.append(
            f'<text x="{_f(rect.cx)}" y="{_f(y)}" font-size="{_f(font)}" '
            f'fill="{style["text"]}" text-anchor="middle" font-weight="{weight}">{_esc(line)}</text>'
        )
    if sub_lines:
        sub_top = top + len(label_lines) * lh + 1.0
        for index, line in enumerate(sub_lines):
            if not line:
                continue
            y = sub_top + index * sub_lh + sub_lh / 2.0 + sub_font * 0.32
            parts.append(
                f'<text x="{_f(rect.cx)}" y="{_f(y)}" font-size="{_f(sub_font)}" '
                f'fill="{style["sub"]}" text-anchor="middle">{_esc(line)}</text>'
            )
    return "\n    ".join(parts)


def _render_note(node: dict[str, Any], rect: Rect, style: dict[str, Any]) -> str:
    font = NOTE_FONT
    lh = _line_height(font)
    lines = node["label_lines"] + node["sub_lines"]
    block_h = len(lines) * lh
    top = rect.cy - block_h / 2.0
    parts = []
    for index, line in enumerate(lines):
        if not line:
            continue
        y = top + index * lh + lh / 2.0 + font * 0.32
        parts.append(
            f'<text x="{_f(rect.cx)}" y="{_f(y)}" font-size="{_f(font)}" '
            f'fill="{style["text"]}" text-anchor="middle">{_esc(line)}</text>'
        )
    return "\n    ".join(parts)


def _render_group(group: dict[str, Any]) -> str:
    rect: Rect = group["rect"]
    if rect is None:
        return ""
    fill, stroke, label_color = GROUP_TINTS.get(group["color"], GROUP_TINTS["gray"])
    parts = [
        f'<rect x="{_f(rect.x)}" y="{_f(rect.y)}" width="{_f(rect.w)}" height="{_f(rect.h)}" '
        f'rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{_f(PANEL_STROKE_W)}" '
        f'stroke-dasharray="3.2 2.2"/>'
    ]
    label = group["label"]
    if label:
        width = _text_width(label, GROUP_FONT, bold=True) + 9
        x0 = rect.x + 5.0
        y0 = rect.y - 7.8
        parts.append(
            f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(width)}" height="10" rx="2.5" '
            f'fill="#FFFFFF"/>'
        )
        parts.append(
            f'<text x="{_f(x0 + 4.5)}" y="{_f(rect.y + 0.4)}" font-size="{_f(GROUP_FONT)}" '
            f'fill="{label_color}" text-anchor="start" font-weight="600">{_esc(label)}</text>'
        )
    return "\n    ".join(parts)


def _edge_markers(edges: list[dict[str, Any]]) -> dict[str, str]:
    colors = {edge["style"]["color"] for edge in edges}
    markers: dict[str, str] = {}
    for color in sorted(colors):
        marker_id = _marker_id(color)
        markers[marker_id] = (
            f'<marker id="{marker_id}" viewBox="0 0 10 10" refX="9.4" refY="5" '
            f'markerWidth="5.2" markerHeight="5.2" orient="auto-start-reverse">'
            f'<path d="M 0.8 1 L 9.4 5 L 0.8 9 Z" fill="{color}"/></marker>'
        )
    return markers


def _arrowhead_polygon(edge: dict[str, Any]) -> str:
    """显式三角形箭头：不依赖 SVG marker，所有栅格化器都能正确渲染。"""
    points = edge.get("points") or []
    if len(points) < 2:
        return ""
    tip = points[-1]
    previous = points[-2]
    dx = tip[0] - previous[0]
    dy = tip[1] - previous[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return ""
    ux, uy = dx / length, dy / length
    base = (tip[0] - ux * 4.6, tip[1] - uy * 4.6)
    half = 2.0
    left = (base[0] - uy * half, base[1] + ux * half)
    right = (base[0] + uy * half, base[1] - ux * half)
    color = edge["style"]["color"]
    coords = " ".join(f"{_f(x)},{_f(y)}" for x, y in (tip, left, right))
    return f'<polygon class="arrowhead" points="{coords}" fill="{color}"/>'


def _render_edge(edge: dict[str, Any]) -> str:
    style = edge["style"]
    dash = ' stroke-dasharray="3.2 2.4"' if style["dashed"] else ""
    marker = f' marker-end="url(#{_marker_id(style["color"])})"'
    path = edge["path"]
    if not path:
        return ""
    line = (
        f'<path d="{path}" fill="none" stroke="{style["color"]}" '
        f'stroke-width="{_f(EDGE_WIDTH)}" stroke-linecap="round" '
        f'stroke-linejoin="round"{dash}{marker}/>'
    )
    arrow = _arrowhead_polygon(edge)
    return f"{line}\n    {arrow}" if arrow else line


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return not (a.right <= b.x or b.right <= a.x or a.bottom <= b.y or b.bottom <= a.y)


def _place_edge_labels(layout: _Layout) -> None:
    """为每条边标签挑选一个尽量不压节点、不压分组边框、不互相重叠的位置。"""
    node_rects = [
        node["rect"] for node in layout.nodes.values() if node["kind"] != "note"
    ]
    group_rects = [
        group["rect"] for group in layout.groups.values() if group.get("rect") is not None
    ]
    group_label_rects: list[Rect] = []
    for group in layout.groups.values():
        if group.get("rect") is not None and group.get("label"):
            width = _text_width(group["label"], GROUP_FONT, bold=True) + 10
            group_label_rects.append(
                Rect(group["rect"].x + 5.0, group["rect"].y - 7.8, width, 10.0)
            )
    placed: list[Rect] = []
    for edge in layout.edges:
        label = edge["label"]
        if not label:
            continue
        width = _text_width(label, EDGE_LABEL_FONT) + 9.0
        height = 11.0
        points = edge["points"]
        candidates: list[tuple[float, float]] = []
        anchor = edge.get("label_at")
        if anchor:
            candidates.append(anchor)
        for fraction in (0.5, 0.42, 0.58, 0.34, 0.66, 0.28, 0.72):
            candidates.append(_point_at(points, fraction))
        src_rect = layout.nodes.get(edge["from"], {}).get("rect")
        dst_rect = layout.nodes.get(edge["to"], {}).get("rect")
        if src_rect is not None and dst_rect is not None:
            above_y = min(src_rect.y, dst_rect.y) - GROUP_PAD - 6.0
            below_y = max(src_rect.bottom, dst_rect.bottom) + GROUP_PAD + 6.0
            mid_x = candidates[1][0] if len(candidates) > 1 else candidates[0][0]
            for offset in (0.0, -18.0, 18.0):
                candidates.append((mid_x + offset, above_y))
                candidates.append((mid_x + offset, below_y))
        best = candidates[0]
        best_score = float("inf")
        for cx, cy in candidates:
            label_rect = Rect(cx - width / 2.0, cy - height / 2.0, width, height)
            score = 0.0
            for rect in node_rects:
                if _rects_overlap(label_rect, rect.expand(2.0)):
                    score += 30.0
            for rect in group_rects:
                near_border = min(
                    abs(cy - rect.y),
                    abs(cy - rect.bottom),
                    abs(cx - rect.x),
                    abs(cx - rect.right),
                )
                if near_border < 6.0:
                    score += 7.0
            for other in placed:
                if _rects_overlap(label_rect, other.expand(2.0)):
                    score += 25.0
            for label_rect_obstacle in group_label_rects:
                if _rects_overlap(label_rect, label_rect_obstacle):
                    score += 14.0
            if score < best_score - 1e-9:
                best_score = score
                best = (cx, cy)
        edge["label_pos"] = best
        placed.append(Rect(best[0] - width / 2.0, best[1] - height / 2.0, width, height))


def _render_edge_label(edge: dict[str, Any]) -> str:
    label = edge["label"]
    if not label:
        return ""
    x, y = edge.get("label_pos") or edge.get("label_at") or (0.0, 0.0)
    width = _text_width(label, EDGE_LABEL_FONT) + 9
    height = 11.0
    parts = [
        f'<rect x="{_f(x - width / 2.0)}" y="{_f(y - height / 2.0)}" width="{_f(width)}" '
        f'height="{_f(height)}" rx="2.5" fill="#FFFFFF" opacity="0.97"/>',
        f'<text x="{_f(x)}" y="{_f(y + 2.4)}" font-size="{_f(EDGE_LABEL_FONT)}" '
        f'fill="{INK_SOFT}" text-anchor="middle">{_esc(label)}</text>',
    ]
    return "\n    ".join(parts)


def _legend_entry_style(item: str) -> tuple[str, str, str]:
    if item == "accent":
        return ACCENT_STYLE["fill"], ACCENT_STYLE["stroke"], "Key component"
    style = KIND_STYLE[item]
    fill = style["fill"] if style["fill"] != "none" else "#FFFFFF"
    stroke = style["stroke"] if style["stroke"] != "none" else PANEL_STROKE
    return fill, stroke, KIND_LEGEND[item]


def _render_legend(items: list[str], center_x: float, top_y: float) -> str:
    if not items:
        return ""
    gap = 13.0
    entries = []
    total = 0.0
    for item in items:
        fill, stroke, label = _legend_entry_style(item)
        width = 13.0 + 4.0 + _text_width(label, LEGEND_FONT) + gap
        entries.append((item, fill, stroke, label, width))
        total += width
    total -= gap
    x = center_x - total / 2.0
    parts: list[str] = []
    for _item, fill, stroke, label, width in entries:
        parts.append(
            f'<rect x="{_f(x)}" y="{_f(top_y)}" width="13" height="9" rx="2.5" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>'
        )
        parts.append(
            f'<text x="{_f(x + 17)}" y="{_f(top_y + 6.6)}" font-size="{_f(LEGEND_FONT)}" '
            f'fill="{INK_SOFT}">{_esc(label)}</text>'
        )
        x += width
    return "\n    ".join(parts)


def _resolve_max_width(spec: dict[str, Any]) -> float:
    raw = spec.get("width")
    if raw is None:
        return DEFAULT_MAX_WIDTH
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_WIDTH
    unit = str(spec.get("unit") or spec.get("width_unit") or "").lower()
    if unit in ("px", "pixel", "pixels"):
        value *= 0.75
    elif unit in ("in", "inch", "inches"):
        value *= 72.0
    elif value > 900.0:  # 兼容 {"width": 1200} 这类“像素”写法
        value *= 0.75
    return max(220.0, min(value, MAX_CANVAS_WIDTH))


def _resolve_max_height(spec: dict[str, Any]) -> float | None:
    raw = spec.get("height")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    unit = str(spec.get("unit") or spec.get("width_unit") or "").lower()
    if unit in ("px", "pixel", "pixels"):
        value *= 0.75
    elif unit in ("in", "inch", "inches"):
        value *= 72.0
    elif value > 900.0:
        value *= 0.75
    return max(120.0, min(value, 900.0))


def _canvas_bounds(layout: _Layout) -> Rect:
    rect: Rect | None = None
    for node in layout.nodes.values():
        rect = node["rect"] if rect is None else rect.union(node["rect"])
    for group in layout.groups.values():
        if group.get("rect") is not None:
            rect = rect.union(group["rect"])
    for edge in layout.edges:
        for point in edge["points"]:
            point_rect = Rect(point[0], point[1], 0.0, 0.0)
            rect = rect.union(point_rect)
        if edge.get("label") and edge.get("label_pos"):
            label_x, label_y = edge["label_pos"]
            label_w = _text_width(edge["label"], EDGE_LABEL_FONT) + 9.0
            rect = rect.union(Rect(label_x - label_w / 2.0, label_y - 5.5, label_w, 11.0))
    if rect is None:
        rect = Rect(0.0, 0.0, 1.0, 1.0)
    # 组标题可能溢出面板，给一点余量
    for group in layout.groups.values():
        if group.get("rect") is not None and group.get("label"):
            width = _text_width(group["label"], GROUP_FONT, bold=True) + 10
            rect = rect.union(
                Rect(group["rect"].x + 5.0, group["rect"].y - 8.0, width, 0.0)
            )
    return rect


def build_svg(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """把 spec 渲染为 SVG 字符串；返回 ``(svg, meta)``。"""
    layout = _layout(spec)
    warnings = layout.warnings
    _place_edge_labels(layout)
    body_bounds = _canvas_bounds(layout)
    max_width = _resolve_max_width(spec)
    max_height = _resolve_max_height(spec)

    title = str(spec.get("title") or "")
    subtitle = str(spec.get("subtitle") or "")
    show_legend = spec.get("legend")
    legend_items = layout.legend if show_legend is None or bool(show_legend) else []
    legend_title = str(spec.get("legend_title") or "")

    title_h = 0.0
    if title:
        title_h = TITLE_FONT * 1.35
    if subtitle:
        title_h += SUB_FONT * 1.55
    title_block = title_h + (TITLE_GAP if title_h else 0.0)
    legend_block = (LEGEND_GAP + 10.0) if legend_items else 0.0
    legend_extra = 0.0
    if legend_items:
        width_sum = sum(
            17.0 + _text_width(_legend_entry_style(item)[2], LEGEND_FONT) + 13.0
            for item in legend_items
        )
        if legend_title:
            width_sum += _text_width(legend_title, LEGEND_FONT) + 10
        legend_extra = max(0.0, width_sum - body_bounds.w)

    canvas_w = max(
        body_bounds.w + 2 * CANVAS_MARGIN,
        _text_width(title, TITLE_FONT, bold=True) + 2 * CANVAS_MARGIN,
        body_bounds.w + legend_extra + 2 * CANVAS_MARGIN,
    )
    canvas_h = title_block + body_bounds.h + legend_block + 2 * CANVAS_MARGIN
    if canvas_w <= 0 or canvas_h <= 0:
        raise ValueError("spec 渲染后的画布尺寸非法")

    # 超过目标尺寸时整体等比缩小（通过根节点物理尺寸实现，字号随之缩小）。
    # width/height 都按“上限”解释：内容更小时画布自动收缩，不会留下大片空白。
    scale = 1.0
    if canvas_w > max_width:
        scale = max_width / canvas_w
        warnings.append(
            f"内容自然宽度 {canvas_w:.0f}pt 超过目标 {max_width:.0f}pt，已整体缩放至 "
            f"{scale * 100:.0f}%（字号等效 {BASE_FONT * scale:.1f}pt）。"
            "如需保持 8pt，可减少列数/缩短标签，或显式增大 spec.width。"
        )
    if max_height is not None and canvas_h > max_height:
        height_scale = max_height / canvas_h
        if height_scale < scale:
            warnings.append(
                f"内容自然高度 {canvas_h:.0f}pt 超过目标 {max_height:.0f}pt，已整体缩放至 "
                f"{height_scale * 100:.0f}%。"
            )
        scale = min(scale, height_scale)
    width_pt = canvas_w * scale
    height_pt = canvas_h * scale

    dx = (canvas_w - body_bounds.w) / 2.0 - body_bounds.x
    dy = CANVAS_MARGIN + title_block - body_bounds.y

    # 图例位置
    legend_y = 0.0
    if legend_items:
        legend_y = CANVAS_MARGIN + title_block + body_bounds.h + LEGEND_GAP

    # 标题
    title_parts: list[str] = []
    if title:
        title_parts.append(
            f'<text x="{_f(canvas_w / 2.0)}" y="{_f(CANVAS_MARGIN + TITLE_FONT)}" '
            f'font-size="{_f(TITLE_FONT)}" fill="{INK}" text-anchor="middle" '
            f'font-weight="600">{_esc(title)}</text>'
        )
    if subtitle:
        sub_y = CANVAS_MARGIN + TITLE_FONT * 1.35 + SUB_FONT * 0.9
        title_parts.append(
            f'<text x="{_f(canvas_w / 2.0)}" y="{_f(sub_y)}" font-size="{_f(SUB_FONT)}" '
            f'fill="{MUTED}" text-anchor="middle">{_esc(subtitle)}</text>'
        )

    markers = _edge_markers(layout.edges)
    marker_defs = "\n      ".join(markers.values())

    group_svg = "\n    ".join(
        _render_group(group) for group in layout.groups.values() if group.get("rect")
    )
    edge_svg = "\n    ".join(_render_edge(edge) for edge in layout.edges)
    edge_label_svg = "\n    ".join(
        _render_edge_label(edge)
        for edge in layout.edges
        if layout.nodes.get(edge["from"], {}).get("kind") != "note"
    )
    node_svg = "\n    ".join(_render_node(node) for node in layout.nodes.values())
    legend_svg = _render_legend(legend_items, canvas_w / 2.0, legend_y)
    legend_title_svg = ""
    if legend_items and legend_title:
        legend_title_svg = (
            f'<text x="{_f(canvas_w / 2.0 - sum(17.0 + _text_width(_legend_entry_style(i)[2], LEGEND_FONT) + 13.0 for i in legend_items) / 2.0 - _text_width(legend_title, LEGEND_FONT) - 4)}" '
            f'y="{_f(legend_y + 6.6)}" font-size="{_f(LEGEND_FONT)}" fill="{MUTED}">'
            f"{_esc(legend_title)}</text>"
        )

    title_svg = "\n    ".join(title_parts)
    title_text = title or "Model architecture"
    desc = f"{title_text}（由 SciForge figures.arch 生成）"
    if subtitle:
        desc += f" — {subtitle}"

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{_f(width_pt)}pt" height="{_f(height_pt)}pt"
     viewBox="0 0 {_f(canvas_w)} {_f(canvas_h)}" role="img">
  <title>{_esc(title_text)}</title>
  <desc>{_esc(desc)}</desc>
  <defs>
      {marker_defs}
  </defs>
  <rect x="0" y="0" width="{_f(canvas_w)}" height="{_f(canvas_h)}" fill="#FFFFFF"/>
  <g font-family="Helvetica Neue, Helvetica, Arial, Noto Sans CJK SC, sans-serif"
     text-rendering="optimizeLegibility" shape-rendering="geometricPrecision">
    {title_svg}
    <g transform="translate({_f(dx)} {_f(dy)})">
    {group_svg}
    {edge_svg}
    {node_svg}
    {edge_label_svg}
    </g>
    {legend_title_svg}
    {legend_svg}
  </g>
</svg>
"""
    meta = {
        "width_pt": round(width_pt, 2),
        "height_pt": round(height_pt, 2),
        "canvas_w": round(canvas_w, 2),
        "canvas_h": round(canvas_h, 2),
        "scale": round(scale, 4),
        "warnings": warnings,
        "node_count": len(layout.nodes),
        "edge_count": len(layout.edges),
        "direction": layout.direction,
        "legend": legend_items,
        "formats": ["svg"],
    }
    return svg, meta


# ---------------------------------------------------------------------------
# SVG → PDF / PNG 转换（优雅降级）
# ---------------------------------------------------------------------------

_PNG_FALLBACKS = ("rsvg-convert", "sips", "qlmanage")
_PDF_FALLBACKS = ("rsvg-convert", "inkscape", "sips")
_INSTALL_HINT = (
    "安装任一转换器即可：`pip install cairosvg`（推荐，纯 Python）、"
    "`brew install librsvg`（提供 rsvg-convert）或 `brew install --cask inkscape`。"
    "macOS 下 PDF/PNG 还可以用系统自带的 sips/qlmanage 兜底。"
)


@dataclass
class ArchRenderResult:
    """一次架构图渲染的完整结果（路径 + 降级说明）。"""

    primary: Path
    artifacts: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    spec_path: Path | None = None
    width_pt: float = 0.0
    height_pt: float = 0.0
    formats: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": str(self.primary),
            "artifacts": [str(path) for path in self.artifacts],
            "spec_path": str(self.spec_path) if self.spec_path else "",
            "warnings": list(self.warnings),
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "formats": list(self.formats),
            "note": self.note,
        }


def _load_cairosvg() -> Any | None:
    try:
        import cairosvg  # type: ignore[import-not-found]

        return cairosvg
    except Exception:  # noqa: BLE001 - 未安装/动态库缺失都算不可用
        return None


def _find_binary(name: str) -> str | None:
    return shutil.which(name)


def _convert_with_cairosvg(svg_path: Path, target: Path, fmt: str) -> bool:
    cairosvg = _load_cairosvg()
    if cairosvg is None:
        return False
    try:
        if fmt == "pdf":
            cairosvg.svg2pdf(url=str(svg_path), write_to=str(target))
        else:
            cairosvg.svg2png(url=str(svg_path), write_to=str(target), scale=3.0)
        return target.exists() and target.stat().st_size > 0
    except Exception:  # noqa: BLE001 - 转换失败按降级处理
        return False


def _png_via_sips(svg_path: Path, target: Path, dpi: int) -> bool:
    """macOS 自带 sips：先把 SVG 物理尺寸放大到目标 DPI，再栅格化。"""
    binary = _find_binary("sips")
    if not binary:
        return False
    try:
        text = svg_path.read_text(encoding="utf-8")
        match_w = re.search(r'width="([\d.]+)pt"', text)
        match_h = re.search(r'height="([\d.]+)pt"', text)
        if not match_w or not match_h:
            return False
        width_px = float(match_w.group(1)) / 72.0 * dpi
        height_px = float(match_h.group(1)) / 72.0 * dpi
        scaled = text.replace(
            f'width="{match_w.group(1)}pt"', f'width="{width_px:.1f}"', 1
        ).replace(f'height="{match_h.group(1)}pt"', f'height="{height_px:.1f}"', 1)
        with tempfile.TemporaryDirectory(prefix="sciforge-fig-") as tmp:
            tmp_svg = Path(tmp) / "figure.svg"
            tmp_svg.write_text(scaled, encoding="utf-8")
            tmp_png = Path(tmp) / "figure.png"
            proc = subprocess.run(  # noqa: S603 - 本地已知命令
                [binary, "-s", "format", "png", str(tmp_svg), "--out", str(tmp_png)],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if proc.returncode != 0 or not tmp_png.exists():
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp_png, target)
        return target.exists() and target.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


def _pdf_via_sips(svg_path: Path, target: Path) -> bool:
    """macOS 自带 sips：SVG → PDF（LaTeX 可直接 \\includegraphics）。"""
    binary = _find_binary("sips")
    if not binary:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="sciforge-fig-") as tmp:
            tmp_pdf = Path(tmp) / "figure.pdf"
            proc = subprocess.run(  # noqa: S603 - 本地已知命令
                [binary, "-s", "format", "pdf", str(svg_path), "--out", str(tmp_pdf)],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if proc.returncode != 0 or not tmp_pdf.exists() or tmp_pdf.stat().st_size == 0:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp_pdf, target)
        return target.exists() and target.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


def _png_via_qlmanage(svg_path: Path, target: Path, dpi: int) -> bool:
    """macOS QuickLook 兜底：输出为正方形缩略图（保留留白）。"""
    binary = _find_binary("qlmanage")
    if not binary:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="sciforge-fig-") as tmp:
            proc = subprocess.run(  # noqa: S603
                [binary, "-t", "-s", "2000", "-o", tmp, str(svg_path)],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if proc.returncode != 0:
                return False
            produced = list(Path(tmp).glob("*.png"))
            if not produced:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(produced[0], target)
        return target.exists() and target.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


def _convert_with_binary(svg_path: Path, target: Path, fmt: str, dpi: int) -> bool:
    if fmt == "png":
        rsvg = _find_binary("rsvg-convert")
        if rsvg:
            try:
                proc = subprocess.run(  # noqa: S603
                    [rsvg, "-f", "png", "-d", str(dpi), "-p", str(dpi), "-o", str(target), str(svg_path)],
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
                if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                    return True
            except Exception:  # noqa: BLE001
                pass
        if _png_via_sips(svg_path, target, dpi):
            return True
        return _png_via_qlmanage(svg_path, target, dpi)
    if fmt == "pdf":
        rsvg = _find_binary("rsvg-convert")
        if rsvg:
            try:
                proc = subprocess.run(  # noqa: S603
                    [rsvg, "-f", "pdf", "-o", str(target), str(svg_path)],
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
                if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                    return True
            except Exception:  # noqa: BLE001
                pass
        inkscape = _find_binary("inkscape")
        if inkscape:
            try:
                proc = subprocess.run(  # noqa: S603
                    [inkscape, str(svg_path), f"--export-filename={target}"],
                    capture_output=True,
                    timeout=180,
                    check=False,
                )
                if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                    return True
            except Exception:  # noqa: BLE001
                pass
        if _pdf_via_sips(svg_path, target):
            return True
    return False


def _normalize_formats(
    fmt: str | None,
    formats: list[str] | str | None,
) -> list[str]:
    requested: list[str] = []
    if formats:
        if isinstance(formats, str):
            requested = [part.strip().lower().lstrip(".") for part in formats.split(",") if part.strip()]
        else:
            requested = [str(part).strip().lower().lstrip(".") for part in formats if str(part).strip()]
    elif fmt:
        requested = [fmt.strip().lower().lstrip(".")]
    else:
        requested = ["svg", "pdf", "png"]
    allowed = {"svg", "pdf", "png"}
    cleaned: list[str] = []
    for item in requested:
        if item in allowed and item not in cleaned:
            cleaned.append(item)
    if "svg" not in cleaned:
        cleaned.insert(0, "svg")
    return cleaned


def render_architecture_full(
    spec: dict[str, Any],
    out: str | Path,
    *,
    fmt: str | None = None,
    formats: list[str] | str | None = None,
    dpi: int | None = None,
    write_spec: bool = True,
) -> ArchRenderResult:
    """渲染架构图并返回完整结果（含实际产物与降级说明）。

    - 总会写出 ``<stem>.svg``；
    - ``fmt`` / ``formats`` 指定额外格式（pdf/png），转换器缺失时只保留 SVG 并给出提示；
    - 默认尝试 svg+pdf+png 三件套，逐个优雅降级；
    - spec 落到 ``<stem>.spec.yaml`` 便于复现。
    """
    if not isinstance(spec, dict):
        raise TypeError("spec 必须是 dict（可用 load_spec 读取 YAML）")
    out_path = Path(out).expanduser()
    if out_path.suffix.lower() in (".svg", ".pdf", ".png"):
        stem = out_path.with_suffix("")
    else:
        stem = out_path
    stem.parent.mkdir(parents=True, exist_ok=True)
    svg_path = stem.with_suffix(".svg")

    svg_text, meta = build_svg(spec)
    svg_path.write_text(svg_text, encoding="utf-8")

    requested = _normalize_formats(fmt, formats)
    warnings: list[str] = list(meta["warnings"])
    artifacts: list[Path] = [svg_path]
    produced: dict[str, Path] = {"svg": svg_path}
    effective_dpi = int(dpi or spec.get("dpi") or 300)

    for target_fmt in requested:
        if target_fmt == "svg":
            continue
        target = stem.with_suffix(f".{target_fmt}")
        ok = _convert_with_cairosvg(svg_path, target, target_fmt)
        if not ok:
            ok = _convert_with_binary(svg_path, target, target_fmt, effective_dpi)
        if ok:
            artifacts.append(target)
            produced[target_fmt] = target
        else:
            hint = (
                f"{target_fmt.upper()} 未生成：当前环境没有可用的 SVG→{target_fmt.upper()} 转换器。"
                + _INSTALL_HINT
            )
            if target_fmt in produced:
                continue
            warnings.append(hint)

    # 主产物：请求中的第一优先格式；失败回退到 SVG
    primary_format = next((item for item in requested if item != "svg"), "svg")
    primary = produced.get(primary_format) or svg_path

    spec_path: Path | None = None
    if write_spec:
        spec_path = save_spec(spec, stem.with_suffix(".spec.yaml"))

    note_parts = [f"已生成 {len(artifacts)} 个文件：{', '.join(p.name for p in artifacts)}。"]
    if len(artifacts) < len(requested):
        missing = [item for item in requested if item not in produced]
        note_parts.append(f"降级说明：{'/'.join(missing)} 转换器不可用，仅输出 {'/'.join(p.suffix.lstrip('.') for p in artifacts)}。")
    if meta["scale"] < 0.999:
        note_parts.append(f"画布整体缩放 {meta['scale'] * 100:.0f}%，等效字号 {BASE_FONT * meta['scale']:.1f}pt。")
    result = ArchRenderResult(
        primary=primary,
        artifacts=artifacts,
        warnings=warnings,
        spec_path=spec_path,
        width_pt=meta["width_pt"],
        height_pt=meta["height_pt"],
        formats=[path.suffix.lstrip(".") for path in artifacts],
        note=" ".join(note_parts),
    )
    return result


def render_architecture(
    spec: dict[str, Any],
    out: str | Path,
    *,
    fmt: str | None = None,
) -> Path:
    """按 ARCHITECTURE §11 契约渲染架构图，返回主产物路径。

    需要完整的产物列表/降级说明时用 :func:`render_architecture_full`。
    """
    return render_architecture_full(spec, out, fmt=fmt).primary


# ---------------------------------------------------------------------------
# 内置示例模板（transformer block / 多模态 pipeline / 训练循环）
# ---------------------------------------------------------------------------


def transformer_block_spec() -> dict[str, Any]:
    """纵向 block 示例：Pre-LN Transformer block（含残差旁路）。"""
    return {
        "title": "Pre-LN Transformer Block",
        "direction": "vertical",
        "width": 360,
        "nodes": [
            {"id": "x", "label": "x", "sub": "(B, N, D)", "kind": "data", "row": 0, "col": 0},
            {"id": "ln1", "label": "LayerNorm", "kind": "module", "row": 0, "group": "attn"},
            {"id": "mhsa", "label": "Multi-Head Self-Attention", "kind": "module", "row": 1, "group": "attn"},
            {"id": "add1", "label": "⊕", "kind": "op", "row": 2, "group": "attn"},
            {"id": "ln2", "label": "LayerNorm", "kind": "module", "row": 0, "group": "ffn"},
            {"id": "ffn", "label": "Feed-Forward Network", "kind": "module", "row": 1, "group": "ffn"},
            {"id": "add2", "label": "⊕", "kind": "op", "row": 2, "group": "ffn"},
            {"id": "out", "label": "x'", "sub": "(B, N, D)", "kind": "data", "row": 7, "col": 0},
            {
                "id": "note1",
                "label": "N: sequence length   D: hidden dim",
                "kind": "note",
                "row": 8,
                "col": 0,
            },
        ],
        "groups": [
            {"id": "attn", "label": "Attention sublayer", "row": 1, "col": 0, "color": "blue"},
            {"id": "ffn", "label": "Feed-Forward sublayer", "row": 4, "col": 0, "color": "green"},
        ],
        "edges": [
            {"from": "x", "to": "ln1"},
            {"from": "ln1", "to": "mhsa"},
            {"from": "mhsa", "to": "add1"},
            {"from": "add1", "to": "ln2"},
            {"from": "ln2", "to": "ffn"},
            {"from": "ffn", "to": "add2"},
            {"from": "add2", "to": "out"},
            {"from": "x", "to": "add1", "style": "skip dashed", "offset": 26, "label": "residual"},
            {"from": "add1", "to": "add2", "style": "skip dashed", "offset": 40, "label": "residual"},
        ],
        "legend": True,
    }


def multimodal_pipeline_spec() -> dict[str, Any]:
    """横向 pipeline 示例：CLIP 风格多模态对比学习。"""
    return {
        "title": "Multimodal Contrastive Learning Pipeline",
        "direction": "horizontal",
        "width": 520,
        "nodes": [
            {"id": "img", "label": "Image", "sub": "B × 3 × H × W", "kind": "data", "row": 0, "col": 0, "group": "vis"},
            {"id": "vit", "label": "Vision Encoder", "sub": "ViT-B/16 · 12 layers", "kind": "module", "row": 0, "col": 1, "group": "vis"},
            {"id": "zv", "label": "z_v : B × D", "kind": "tensor", "row": 0, "col": 2, "group": "vis"},
            {"id": "txt", "label": "Text", "sub": "B × L", "kind": "data", "row": 0, "col": 0, "group": "txt"},
            {"id": "tenc", "label": "Text Encoder", "sub": "frozen · 12 layers", "kind": "module", "row": 0, "col": 1, "dashed": True, "group": "txt"},
            {"id": "zt", "label": "z_t : B × D", "kind": "tensor", "row": 0, "col": 2, "group": "txt"},
            {"id": "sim", "label": "Cosine Similarity", "sub": "B × B affinity", "kind": "module", "row": 0, "col": 0, "accent": True, "group": "obj"},
            {"id": "loss", "label": "InfoNCE Loss", "kind": "loss", "row": 1, "col": 0, "group": "obj"},
            {"id": "note1", "label": "D = 512   τ = 0.07   B = batch size", "kind": "note", "row": 2, "col": 0, "span": "all"},
        ],
        "groups": [
            {"id": "vis", "label": "Visual encoding", "row": 0, "col": 0, "color": "blue"},
            {"id": "txt", "label": "Text encoding", "row": 1, "col": 0, "color": "green"},
            {"id": "obj", "label": "Alignment objective", "row": 0, "col": 3, "color": "orange"},
        ],
        "edges": [
            {"from": "img", "to": "vit"},
            {"from": "vit", "to": "zv"},
            {"from": "txt", "to": "tenc"},
            {"from": "tenc", "to": "zt"},
            {"from": "zv", "to": "sim"},
            {"from": "zt", "to": "sim"},
            {"from": "sim", "to": "loss", "label": "logits"},
        ],
        "legend": True,
    }


def training_loop_spec() -> dict[str, Any]:
    """横向 pipeline 示例：训练循环（含回到 Forward 的迭代边）。"""
    return {
        "title": "Training Loop",
        "direction": "horizontal",
        "width": 540,
        "nodes": [
            {"id": "data", "label": "Data Loader", "sub": "B × ...", "kind": "data", "row": 0, "col": 0, "group": "fwd"},
            {"id": "model", "label": "Forward Pass", "kind": "module", "row": 0, "col": 1, "group": "fwd"},
            {"id": "loss", "label": "Loss", "kind": "loss", "row": 0, "col": 0, "group": "loss"},
            {"id": "bwd", "label": "Backward Pass", "kind": "module", "row": 0, "col": 0, "group": "bwd"},
            {"id": "opt", "label": "Optimizer Step", "kind": "module", "row": 0, "col": 1, "group": "bwd"},
            {"id": "note1", "label": "next batch → repeat until convergence", "kind": "note", "row": 1, "col": 0, "span": "all"},
        ],
        "groups": [
            {"id": "fwd", "label": "Forward", "row": 0, "col": 0, "color": "blue"},
            {"id": "loss", "label": "Objective", "row": 0, "col": 2, "color": "orange"},
            {"id": "bwd", "label": "Backward & update", "row": 0, "col": 3, "color": "green"},
        ],
        "edges": [
            {"from": "data", "to": "model"},
            {"from": "model", "to": "loss", "label": "logits"},
            {"from": "loss", "to": "bwd", "label": "grad"},
            {"from": "bwd", "to": "opt"},
            {"from": "opt", "to": "model", "style": "skip dashed", "offset": 30, "side": "top", "label": "next step"},
        ],
        "legend": True,
    }


BUILTIN_TEMPLATES = {
    "transformer_block": transformer_block_spec,
    "multimodal_pipeline": multimodal_pipeline_spec,
    "training_loop": training_loop_spec,
}


def builtin_template(name: str) -> dict[str, Any]:
    """取内置模板 spec 的深拷贝（可放心修改）。"""
    if name not in BUILTIN_TEMPLATES:
        raise KeyError(f"未知模板 {name!r}，可用：{', '.join(BUILTIN_TEMPLATES)}")
    import copy

    return copy.deepcopy(BUILTIN_TEMPLATES[name]())


def write_builtin_templates(dest: str | Path) -> list[Path]:
    """把内置模板写成 YAML（默认写到 sciforge/templates/figures/）。"""
    out_dir = Path(dest).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name in BUILTIN_TEMPLATES:
        paths.append(save_spec(builtin_template(name), out_dir / f"{name}.yaml"))
    return paths


__all__ = [
    "OKABE_ITO",
    "ArchRenderResult",
    "build_svg",
    "load_spec",
    "save_spec",
    "render_architecture",
    "render_architecture_full",
    "transformer_block_spec",
    "multimodal_pipeline_spec",
    "training_loop_spec",
    "builtin_template",
    "write_builtin_templates",
    "BUILTIN_TEMPLATES",
]
