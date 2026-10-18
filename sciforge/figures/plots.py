"""matplotlib 科研数据图：NeurIPS / CVPR / ICLR 风格。

能力：
- ``kind``: ``line`` | ``bar`` | ``scatter`` | ``radar`` | ``heatmap``，支持多子图 ``panels``；
- 误差带（``std``/``error``）、显著性星标（``significance``）、图例、网格；
- 内置期刊风格（``styles/*.mplstyle``）：无顶/右边框、tick 朝内、细线、7–9pt 字号、
  Okabe-Ito 色盲友好配色、``bbox_inches="tight"``、PDF 字体嵌入（``pdf.fonttype=42``）；
- 固定随机种子，数据全部来自 spec，输出可复现（spec 同时落盘）。

spec 示例::

    kind: line
    title: Worst-group accuracy
    xlabel: Training steps
    ylabel: Accuracy (%)
    style: neurips
    series:
      - {label: Baseline, x: [0, 20, 40], y: [41, 52, 60]}
      - {label: Ours, x: [0, 20, 40], y: [41, 57, 74], std: [0, 1.2, 0.8]}
    significance:
      - {x1: 0, x2: 40, y: 76, label: "**"}
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 在导入 pyplot 之前固定为非交互后端，保证服务器/CI 可渲染
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib  # noqa: E402

if matplotlib.get_backend().lower() not in ("agg", "module://matplotlib_inline.backend_inline"):
    matplotlib.use("Agg")

# fontTools 在写 PDF 子集时会产生无害的 stderr 日志，静默掉以免污染 CLI 输出
logging.getLogger("fontTools").setLevel(logging.ERROR)
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*stringData.*")
warnings.filterwarnings("ignore", message=".*timestamp seems very low.*")

import numpy as np  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# ---------------------------------------------------------------------------
# 配色与风格
# ---------------------------------------------------------------------------

OKABE_ITO = [
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black
]
OKABE_LIGHT = ["#7FB8DA", "#F3C77A", "#7CC9B0", "#EA9F7A", "#E2B0CC", "#A9D8F0", "#F7EF9C", "#999999"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
LINESTYLES = ["-", "--", "-.", ":"]
HATCHES = ["", "//", "..", "xx", "\\\\", "oo"]
SIGNIFICANCE_STARS = {1: "*", 2: "**", 3: "***"}

STYLES_DIR = Path(__file__).resolve().parent / "styles"
DEFAULT_STYLE = "neurips"
PALETTES = {"okabe-ito": OKABE_ITO, "okabe": OKABE_ITO, "colorblind": OKABE_ITO}

BASE_RCPARAMS: dict[str, Any] = {
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "figure.figsize": (3.25, 2.2),
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial", "Noto Sans CJK SC"],
    "font.size": 8,
    "axes.titlesize": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "axes.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": False,
    "grid.linewidth": 0.4,
    "grid.alpha": 0.55,
    "grid.color": "#D3D9E0",
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "xtick.major.size": 2.6,
    "ytick.major.size": 2.6,
    "xtick.minor.size": 1.4,
    "ytick.minor.size": 1.4,
    "legend.frameon": False,
    "legend.handlelength": 1.7,
    "legend.borderaxespad": 0.35,
    "lines.linewidth": 1.2,
    "lines.markersize": 3.0,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "path",
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
    "mathtext.fontset": "dejavusans",
    "axes.facecolor": "white",
    "figure.facecolor": "white",
}

_HEAT_CMAP = LinearSegmentedColormap.from_list(
    "sciforge_blues",
    ["#F7FBFF", "#D8E8F4", "#93C0DE", "#3F87BC", "#0B4C76"],
)
_DIVERGING_CMAP = LinearSegmentedColormap.from_list(
    "sciforge_div",
    ["#0B4C76", "#5C9BC4", "#F2F4F6", "#E9A15D", "#A83C00"],
)


def _style_path(style: str) -> Path:
    return STYLES_DIR / f"{style}.mplstyle"


def available_styles() -> list[str]:
    if not STYLES_DIR.exists():
        return [DEFAULT_STYLE]
    return sorted(path.stem for path in STYLES_DIR.glob("*.mplstyle"))


def apply_style(style: str | None = None, *, palette: str = "okabe-ito") -> str:
    """应用论文风格；返回实际生效的风格名。"""
    name = (style or DEFAULT_STYLE).strip().lower()
    path = _style_path(name)
    if path.exists():
        plt.style.use(str(path))
        # style 文件里可能没有覆盖到的键用基础 rc 兜底
        for key, value in BASE_RCPARAMS.items():
            if key not in plt.rcParams or plt.rcParams[key] == matplotlib.rcParamsDefault.get(key):
                plt.rcParams[key] = value
        effective = name
    else:
        plt.rcParams.update(BASE_RCPARAMS)
        effective = "default"
    colors = PALETTES.get(palette.lower(), OKABE_ITO)
    plt.rcParams["axes.prop_cycle"] = matplotlib.cycler(color=colors)
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42
    plt.rcParams["svg.fonttype"] = "path"
    return effective


# ---------------------------------------------------------------------------
# spec 读取/保存与公共辅助
# ---------------------------------------------------------------------------


def load_spec(path: str | Path) -> dict[str, Any]:
    spec_path = Path(path).expanduser()
    if not spec_path.exists():
        raise FileNotFoundError(f"spec 文件不存在：{spec_path}")
    text = spec_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if spec_path.suffix.lower() != ".json" else None
    if data is None:
        import json

        data = json.loads(text)
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


_MATH_SYMBOLS = {
    "↑": r"$\uparrow$",
    "↓": r"$\downarrow$",
    "→": r"$\rightarrow$",
    "←": r"$\leftarrow$",
    "↔": r"$\leftrightarrow$",
    "≥": r"$\geq$",
    "≤": r"$\leq$",
    "±": r"$\pm$",
    "×": r"$\times$",
    "−": r"$-$",
    "∞": r"$\infty$",
    "α": r"$\alpha$",
    "β": r"$\beta$",
    "λ": r"$\lambda$",
    "μ": r"$\mu$",
    "σ": r"$\sigma$",
}


def _texify(text: Any) -> str:
    """把 matplotlib 字体可能缺失的 unicode 符号换成 mathtext。"""
    value = str(text)
    for source, target in _MATH_SYMBOLS.items():
        if source in value:
            value = value.replace(source, target)
    return value


def _color(index: int, spec: dict[str, Any]) -> str:
    explicit = spec.get("colors")
    if isinstance(explicit, (list, tuple)) and explicit:
        return str(explicit[index % len(explicit)])
    colors = PALETTES.get(str(spec.get("palette") or "okabe-ito").lower(), OKABE_ITO)
    return colors[index % len(colors)]


def _as_float_list(values: Any) -> list[float]:
    return [float(v) for v in (values or [])]


def _ci(std: list[float]) -> np.ndarray:
    return np.asarray(std, dtype=float)


def _significance_bracket(
    ax: Any,
    x1: float,
    x2: float,
    y: float,
    label: str = "*",
    *,
    height: float = 0.035,
    color: str = "#333333",
    fontsize: float = 7.5,
) -> None:
    """在 x1..x2 之间画显著性括号 + 星标。"""
    y_range = ax.get_ylim()[1] - ax.get_ylim()[0]
    h = y_range * height
    ax.plot([x1, x1, x2, x2], [y, y + h, y + h, y], color=color, linewidth=0.7, clip_on=False)
    ax.text(
        (x1 + x2) / 2.0,
        y + h * 1.15,
        label,
        ha="center",
        va="bottom",
        fontsize=fontsize,
        color=color,
    )


def _apply_annotations(ax: Any, spec: dict[str, Any]) -> None:
    """在图上标注关键点（annotations: [{x, y, text, color, arrow}]）。"""
    for item in spec.get("annotations") or []:
        if not isinstance(item, dict):
            continue
        x = item.get("x")
        y = item.get("y")
        if x is None or y is None:
            continue
        text = _texify(item.get("text") or "")
        color = str(item.get("color") or "#333333")
        offset = item.get("xytext") or (4, 6)
        arrowprops = None
        if item.get("arrow", True):
            arrowprops = {
                "arrowstyle": "-|>",
                "color": color,
                "linewidth": 0.6,
                "shrinkA": 1,
                "shrinkB": 2,
            }
        ax.annotate(
            text,
            xy=(float(x), float(y)),
            xytext=tuple(offset),
            textcoords="offset points",
            fontsize=float(item.get("fontsize") or 6.5),
            color=color,
            arrowprops=arrowprops,
            bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.78},
        )


def _apply_common_axes(ax: Any, spec: dict[str, Any], *, grid: bool | None = None) -> None:
    ax.set_xlabel(_texify(spec.get("xlabel") or ""))
    ax.set_ylabel(_texify(spec.get("ylabel") or ""))
    if spec.get("title"):
        ax.set_title(_texify(spec["title"]), loc=str(spec.get("title_loc") or "center"))
    if spec.get("xlim"):
        ax.set_xlim(*_as_float_list(spec["xlim"]))
    if spec.get("ylim"):
        ax.set_ylim(*_as_float_list(spec["ylim"]))
    if spec.get("xticks") is not None:
        ax.set_xticks(_as_float_list(spec["xticks"]))
    if spec.get("xticklabels"):
        ax.set_xticklabels([str(v) for v in spec["xticklabels"]])
    if grid is None:
        grid = bool(spec.get("grid", True))
    if grid:
        ax.grid(True, axis=str(spec.get("grid_axis") or "y"), linestyle="-")
    else:
        ax.grid(False)
    ax.set_axisbelow(True)
    if spec.get("logx"):
        ax.set_xscale("log")
    if spec.get("logy"):
        ax.set_yscale("log")
    if spec.get("spines") == "all":  # 热图等需要完整边框
        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(True)
    _apply_annotations(ax, spec)
    legend = spec.get("legend")
    if legend is not False:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(
                loc=str(spec.get("legend_loc") or "best"),
                ncols=int(spec.get("legend_ncols") or 1),
            )


def series_label(series: dict[str, Any], index: int) -> str:
    """兼容 label / name 两种写法。"""
    for key in ("label", "name"):
        value = series.get(key)
        if value:
            return _texify(value)
    return f"series {index + 1}"


def series_error(series: dict[str, Any]) -> Any | None:
    """兼容 std / error / yerr 三种误差写法。"""
    for key in ("std", "error", "yerr", "stddev"):
        value = series.get(key)
        if value is not None:
            return value
    return None


def _get_series(spec: dict[str, Any]) -> list[dict[str, Any]]:
    series = spec.get("series")
    if isinstance(series, dict):
        series = [series]
    if not series:
        raise ValueError("spec 缺少 series（至少一条数据序列）")
    return [dict(item) for item in series if isinstance(item, dict)]


def _series_xy(series: dict[str, Any], *, fallback_len: int = 0) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(_as_float_list(series.get("y") or series.get("values") or series.get("mean")), dtype=float)
    x = series.get("x")
    if x is None:
        x_arr = np.arange(fallback_len or len(y), dtype=float)
    else:
        x_arr = np.asarray(_as_float_list(x), dtype=float)
    if len(x_arr) != len(y):
        raise ValueError(f"序列 {series_label(series, 0)} 的 x/y 长度不一致：{len(x_arr)} vs {len(y)}")
    return x_arr, y


# ---------------------------------------------------------------------------
# 各类图
# ---------------------------------------------------------------------------


def _plot_line(ax: Any, spec: dict[str, Any]) -> None:
    for index, series in enumerate(_get_series(spec)):
        x, y = _series_xy(series)
        color = series.get("color") or _color(index, spec)
        label = series_label(series, index)
        linestyle = str(series.get("linestyle") or LINESTYLES[index % len(LINESTYLES)])
        marker = series.get("marker", MARKERS[index % len(MARKERS)])
        line = ax.plot(
            x,
            y,
            color=color,
            linestyle=linestyle,
            marker=marker,
            markersize=float(series.get("markersize") or plt.rcParams["lines.markersize"]),
            markevery=series.get("markevery"),
            label=label,
            zorder=3,
        )[0]
        std = series_error(series)
        if std is not None:
            if isinstance(std, (int, float)):
                std_arr = np.full_like(y, float(std))
            else:
                std_arr = _ci(std)
            if len(std_arr) != len(y):
                raise ValueError(f"序列 {label} 的 std 长度与 y 不一致")
            ci = float(series.get("ci") or 1.0)
            ax.fill_between(
                x,
                y - std_arr * ci,
                y + std_arr * ci,
                color=color,
                alpha=float(series.get("band_alpha") or 0.16),
                linewidth=0,
                zorder=2,
            )
        # 末端数值标注（可选）
        if series.get("annotate_last"):
            ax.annotate(
                f"{y[-1]:.1f}",
                xy=(x[-1], y[-1]),
                xytext=(3, 2),
                textcoords="offset points",
                fontsize=6.5,
                color=color,
            )
        del line
    _apply_common_axes(ax, spec)


def _plot_bar(ax: Any, spec: dict[str, Any]) -> None:
    categories = [str(c) for c in (spec.get("categories") or spec.get("xticklabels") or [])]
    series = _get_series(spec)
    n_series = len(series)
    width = float(spec.get("bar_width") or min(0.8 / max(n_series, 1), 0.32))
    x = np.arange(len(categories), dtype=float)
    for index, item in enumerate(series):
        values = np.asarray(_as_float_list(item.get("values") or item.get("y")), dtype=float)
        std = series_error(item)
        std_arr = None
        if std is not None:
            std_arr = np.full_like(values, float(std)) if isinstance(std, (int, float)) else _ci(std)
        offset = (index - (n_series - 1) / 2.0) * width
        color = item.get("color") or _color(index, spec)
        bars = ax.bar(
            x + offset,
            values,
            width=width,
            color=color,
            edgecolor="#333333",
            linewidth=0.4,
            hatch=HATCHES[index % len(HATCHES)] or None,
            alpha=float(item.get("alpha") or 0.92),
            label=series_label(item, index),
            yerr=std_arr,
            error_kw={"elinewidth": 0.6, "capsize": 1.8, "capthick": 0.6},
            zorder=3,
        )
        if spec.get("annotate") or item.get("annotate"):
            for bar, value in zip(bars, values, strict=False):
                ax.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    value,
                    f"{value:.1f}",
                    ha="center",
                    va="bottom",
                    fontsize=6.5,
                )
    if categories:
        ax.set_xticks(x)
        ax.set_xticklabels(categories, rotation=float(spec.get("xtick_rotation") or 0))
    if spec.get("orient") == "horizontal":
        ax.set_yticks(x)
        ax.set_yticklabels(categories)
        ax.set_xticks([])
    _apply_common_axes(ax, spec)


def _plot_scatter(ax: Any, spec: dict[str, Any]) -> None:
    for index, series in enumerate(_get_series(spec)):
        x = np.asarray(_as_float_list(series.get("x")), dtype=float)
        y = np.asarray(_as_float_list(series.get("y")), dtype=float)
        if len(x) != len(y):
            raise ValueError(f"散点序列 {series.get('label', '?')} 的 x/y 长度不一致")
        color = series.get("color") or _color(index, spec)
        sizes = series.get("sizes") or series.get("size") or 18
        ax.scatter(
            x,
            y,
            s=sizes,
            color=color,
            marker=series.get("marker") or MARKERS[index % len(MARKERS)],
            alpha=float(series.get("alpha") or 0.85),
            edgecolors="white",
            linewidths=0.4,
            label=series_label(series, index),
            zorder=3,
        )
    _apply_common_axes(ax, spec)


def _plot_radar(ax: Any, spec: dict[str, Any]) -> None:
    categories = [str(c) for c in (spec.get("categories") or [])]
    if len(categories) < 3:
        raise ValueError("radar 图需要至少 3 个 categories")
    angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False).tolist()
    angles += angles[:1]
    for index, series in enumerate(_get_series(spec)):
        values = _as_float_list(series.get("values") or series.get("y"))
        if len(values) != len(categories):
            raise ValueError(f"radar 序列 {series.get('label', '?')} 的数值个数与 categories 不一致")
        values = values + values[:1]
        color = series.get("color") or _color(index, spec)
        ax.plot(angles, values, color=color, linewidth=1.1, label=str(series.get("label") or "series"))
        ax.fill(angles, values, color=color, alpha=0.10)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=7)
    ax.set_yticklabels([])
    if spec.get("ylim"):
        ax.set_ylim(*_as_float_list(spec["ylim"]))
    ax.grid(True, linewidth=0.4, alpha=0.5)
    if spec.get("legend") is not False:
        ax.legend(loc=str(spec.get("legend_loc") or "upper right"), bbox_to_anchor=(1.15, 1.12))


def _plot_heatmap(ax: Any, spec: dict[str, Any]) -> None:
    matrix = spec.get("matrix") or spec.get("data")
    if matrix is None:
        raise ValueError("heatmap 需要 spec.matrix")
    data = np.asarray(matrix, dtype=float)
    cmap_name = str(spec.get("cmap") or ("div" if spec.get("diverging") else "blues"))
    cmap = _DIVERGING_CMAP if cmap_name in ("div", "diverging") else _HEAT_CMAP
    vmin = spec.get("vmin")
    vmax = spec.get("vmax")
    image = ax.imshow(
        data,
        cmap=cmap,
        aspect=str(spec.get("aspect") or "auto"),
        vmin=float(vmin) if vmin is not None else None,
        vmax=float(vmax) if vmax is not None else None,
        interpolation="nearest",
    )
    if spec.get("xticklabels"):
        ax.set_xticks(range(len(spec["xticklabels"])))
        ax.set_xticklabels([_texify(v) for v in spec["xticklabels"]], rotation=float(spec.get("xtick_rotation") or 0))
    if spec.get("yticklabels"):
        ax.set_yticks(range(len(spec["yticklabels"])))
        ax.set_yticklabels([str(v) for v in spec["yticklabels"]])
    ax.set_xlabel(_texify(spec.get("xlabel") or ""))
    ax.set_ylabel(_texify(spec.get("ylabel") or ""))
    if spec.get("title"):
        ax.set_title(_texify(spec["title"]), loc=str(spec.get("title_loc") or "center"))
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(True)
    ax.tick_params(direction="in", length=2.5)
    if spec.get("colorbar", True):
        colorbar = ax.figure.colorbar(image, ax=ax, fraction=0.045, pad=0.02)
        colorbar.ax.tick_params(labelsize=6.5, direction="in")
        colorbar.outline.set_linewidth(0.5)
        colorbar.set_label(str(spec.get("colorbar_label") or ""), fontsize=7)
    if spec.get("annotate", True) and data.size <= 144:
        threshold = (data.max() + data.min()) / 2.0
        for row in range(data.shape[0]):
            for col in range(data.shape[1]):
                value = data[row, col]
                ax.text(
                    col,
                    row,
                    f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}",
                    ha="center",
                    va="center",
                    fontsize=6,
                    color="white" if value > threshold else "#1B1B1B",
                )


_DISPATCH = {
    "line": _plot_line,
    "bar": _plot_bar,
    "scatter": _plot_scatter,
    "radar": _plot_radar,
    "heatmap": _plot_heatmap,
}


def _draw_single(ax: Any, spec: dict[str, Any]) -> None:
    kind = str(spec.get("kind") or "line").lower()
    if kind not in _DISPATCH:
        raise ValueError(f"不支持的图类型 kind={kind!r}，可用：{', '.join(_DISPATCH)}")
    _DISPATCH[kind](ax, spec)
    significance = spec.get("significance") or []
    if significance:
        for item in significance:
            if not isinstance(item, dict):
                continue
            x1 = float(item.get("x1", 0))
            x2 = float(item.get("x2", 1))
            y = float(item.get("y") or ax.get_ylim()[1])
            label = str(item.get("label") or SIGNIFICANCE_STARS.get(int(item.get("level", 1)), "*"))
            _significance_bracket(ax, x1, x2, y, label)


# ---------------------------------------------------------------------------
# 结果对象与渲染入口
# ---------------------------------------------------------------------------


@dataclass
class PlotRenderResult:
    primary: Path
    artifacts: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    spec_path: Path | None = None
    style: str = ""
    width_in: float = 0.0
    height_in: float = 0.0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": str(self.primary),
            "artifacts": [str(p) for p in self.artifacts],
            "spec_path": str(self.spec_path) if self.spec_path else "",
            "warnings": list(self.warnings),
            "style": self.style,
            "width_in": self.width_in,
            "height_in": self.height_in,
            "note": self.note,
        }


def _normalize_formats(out: Path, spec: dict[str, Any]) -> list[str]:
    raw = spec.get("formats")
    if raw:
        formats = [str(item).strip().lower().lstrip(".") for item in raw]
    else:
        suffix = out.suffix.lower().lstrip(".")
        formats = [suffix] if suffix in ("pdf", "svg", "png") else ["pdf"]
    allowed = {"pdf", "svg", "png"}
    formats = [item for item in formats if item in allowed]
    return formats or ["pdf"]


def render_plot_full(
    spec: dict[str, Any],
    out: str | Path,
    *,
    style: str | None = None,
    write_spec: bool = True,
) -> PlotRenderResult:
    """渲染数据图并返回产物列表（多格式同时输出）。"""
    if not isinstance(spec, dict):
        raise TypeError("spec 必须是 dict（可用 load_spec 读取 YAML）")
    out_path = Path(out).expanduser()
    stem = out_path.with_suffix("") if out_path.suffix.lower() in (".pdf", ".svg", ".png") else out_path
    stem.parent.mkdir(parents=True, exist_ok=True)

    style_name = str(style or spec.get("style") or DEFAULT_STYLE)
    effective_style = apply_style(style_name, palette=str(spec.get("palette") or "okabe-ito"))
    np.random.seed(int(spec.get("seed") or 0))

    warnings: list[str] = []
    panels = spec.get("panels")
    figsize = spec.get("figsize")
    if panels:
        count = len(panels)
        ncols = int(spec.get("ncols") or count)
        nrows = int(spec.get("nrows") or int(np.ceil(count / ncols)))
        if figsize:
            fig_size = tuple(float(v) for v in figsize)
        else:
            fig_size = (3.25 * ncols, 2.15 * nrows)
        fig, axes = plt.subplots(nrows, ncols, figsize=fig_size, squeeze=False)
        flat_axes = [ax for row in axes for ax in row]
        for index, panel in enumerate(panels):
            if not isinstance(panel, dict):
                continue
            panel_spec = {**panel}
            panel_spec.setdefault("palette", spec.get("palette") or "okabe-ito")
            _draw_single(flat_axes[index], panel_spec)
        for ax in flat_axes[count:]:
            ax.axis("off")
        if spec.get("legend") is not False and spec.get("shared_legend"):
            handles: list[Any] = []
            labels: list[str] = []
            for ax in flat_axes[:count]:
                h, lab = ax.get_legend_handles_labels()
                for handle, label in zip(h, lab, strict=False):
                    if label not in labels:
                        handles.append(handle)
                        labels.append(label)
            if handles:
                fig.legend(
                    handles,
                    labels,
                    loc=str(spec.get("legend_loc") or "upper center"),
                    ncols=int(spec.get("legend_ncols") or len(labels)),
                    bbox_to_anchor=(0.5, float(spec.get("legend_anchor_y") or 1.03)),
                )
        if spec.get("suptitle"):
            fig.suptitle(_texify(spec["suptitle"]), fontsize=float(spec.get("suptitle_size") or 8.5))
    else:
        fig_size = tuple(float(v) for v in (figsize or (3.25, 2.2)))
        fig, ax = plt.subplots(figsize=fig_size)
        panel_spec = {**spec}
        panel_spec.setdefault("palette", spec.get("palette") or "okabe-ito")
        _draw_single(ax, panel_spec)

    fig.tight_layout(pad=0.25)
    formats = _normalize_formats(out_path, spec)
    dpi = int(spec.get("dpi") or 300)
    artifacts: list[Path] = []
    for fmt in formats:
        target = stem.with_suffix(f".{fmt}")
        try:
            fig.savefig(
                target,
                format=fmt,
                bbox_inches="tight",
                pad_inches=float(spec.get("pad_inches") or 0.02),
                dpi=dpi,
            )
            if target.exists() and target.stat().st_size > 0:
                artifacts.append(target)
        except Exception as exc:  # noqa: BLE001 - 单一格式失败不应中断
            warnings.append(f"{fmt.upper()} 保存失败：{type(exc).__name__}: {exc}")
    plt.close(fig)

    if not artifacts:
        raise RuntimeError(f"图像没有成功保存任何格式（{formats}）")

    primary_suffix = out_path.suffix.lower().lstrip(".")
    primary = next(
        (path for path in artifacts if path.suffix.lower().lstrip(".") == primary_suffix),
        artifacts[0],
    )
    spec_path = None
    if write_spec:
        spec_path = save_spec(spec, stem.with_suffix(".spec.yaml"))

    width_in, height_in = (round(float(v), 2) for v in fig_size)
    result = PlotRenderResult(
        primary=primary,
        artifacts=artifacts,
        warnings=warnings,
        spec_path=spec_path,
        style=effective_style,
        width_in=width_in,
        height_in=height_in,
        note=f"已生成 {len(artifacts)} 个文件：{', '.join(p.name for p in artifacts)}。",
    )
    return result


def render_plot(spec: dict[str, Any], out: str | Path, *, style: str | None = None) -> Path:
    """按 ARCHITECTURE §11 契约渲染数据图，返回主产物路径。"""
    return render_plot_full(spec, out, style=style).primary


# ---------------------------------------------------------------------------
# 内置示例模板
# ---------------------------------------------------------------------------


def builtin_spec(kind: str = "line") -> dict[str, Any]:
    """返回一个可直接渲染的示例 spec。"""
    if kind == "line":
        return {
            "kind": "line",
            "title": "Worst-group accuracy vs. training steps",
            "xlabel": "Training steps",
            "ylabel": "Worst-group accuracy (%)",
            "style": "neurips",
            "series": [
                {"label": "Baseline", "x": [0, 20, 40, 60, 80, 100], "y": [41.0, 52.4, 59.8, 63.5, 65.1, 66.0]},
                {
                    "label": "Ours",
                    "x": [0, 20, 40, 60, 80, 100],
                    "y": [41.2, 56.8, 65.4, 70.9, 73.2, 74.1],
                    "std": [0, 1.2, 1.0, 0.9, 0.8, 0.7],
                },
                {
                    "label": "Ours w/o router",
                    "x": [0, 20, 40, 60, 80, 100],
                    "y": [41.0, 54.1, 62.2, 66.8, 68.5, 69.3],
                    "std": [0, 1.4, 1.2, 1.0, 0.9, 0.8],
                },
            ],
            "significance": [{"x1": 80, "x2": 100, "y": 78.0, "label": "**"}],
        }
    if kind == "bar":
        return {
            "kind": "bar",
            "title": "Ablation study",
            "ylabel": "Worst-group accuracy (%)",
            "categories": ["Ours", "w/o router", "w/o consistency", "Baseline"],
            "series": [{"label": "Top-1", "values": [74.1, 69.3, 71.6, 66.0]}],
            "ylim": [60, 78],
            "annotate": True,
        }
    if kind == "heatmap":
        return {
            "kind": "heatmap",
            "title": "Confusion matrix",
            "matrix": [[92, 3, 2, 3], [4, 88, 6, 2], [2, 5, 90, 3], [1, 2, 4, 93]],
            "xticklabels": ["A", "B", "C", "D"],
            "yticklabels": ["A", "B", "C", "D"],
            "xlabel": "Predicted",
            "ylabel": "True",
            "colorbar_label": "%",
        }
    if kind == "radar":
        return {
            "kind": "radar",
            "categories": ["Acc", "Robust", "Calib", "Efficiency", "Fairness"],
            "series": [
                {"label": "Baseline", "values": [0.62, 0.48, 0.55, 0.78, 0.45]},
                {"label": "Ours", "values": [0.74, 0.71, 0.68, 0.69, 0.72]},
            ],
        }
    raise ValueError(f"未知示例图类型：{kind!r}")


WRITE_BUILTIN_TEMPLATES = ("line", "bar", "heatmap", "radar")


def write_builtin_templates(dest: str | Path) -> list[Path]:
    out_dir = Path(dest).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for kind in WRITE_BUILTIN_TEMPLATES:
        paths.append(save_spec(builtin_spec(kind), out_dir / f"plot_{kind}.yaml"))
    return paths


__all__ = [
    "OKABE_ITO",
    "PlotRenderResult",
    "apply_style",
    "available_styles",
    "builtin_spec",
    "load_spec",
    "render_plot",
    "render_plot_full",
    "save_spec",
    "write_builtin_templates",
]
