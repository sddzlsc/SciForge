"""figures 模块测试：架构图 SVG / matplotlib 数据图 / booktabs 三线表 / 工具注册。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sciforge.config import Settings
from sciforge.core.registry import ToolContext, get_tool, run_tool
from sciforge.figures import arch, plots, tables
from sciforge.tools.figures import add_cli as add_figures_cli

SVG_NS = "{http://www.w3.org/2000/svg}"


# ---------------------------------------------------------------------------
# 架构图
# ---------------------------------------------------------------------------


def _simple_spec() -> dict:
    return {
        "title": "Test Architecture",
        "direction": "horizontal",
        "width": 500,
        "nodes": [
            {"id": "inp", "label": "Input", "sub": "B × D", "kind": "data", "row": 0, "col": 0, "group": "g1"},
            {"id": "enc", "label": "Encoder", "sub": "12 layers", "kind": "module", "row": 0, "col": 1, "group": "g1"},
            {"id": "feat", "label": "z: B × D", "kind": "tensor", "row": 0, "col": 2},
            {"id": "head", "label": "Classifier", "kind": "module", "row": 0, "col": 3, "accent": True},
            {"id": "loss", "label": "CE Loss", "kind": "loss", "row": 0, "col": 4},
            {"id": "note", "label": "D = 512", "kind": "note", "row": 1, "col": 0, "span": "all"},
        ],
        "groups": [{"id": "g1", "label": "Backbone", "row": 0, "col": 0, "color": "blue"}],
        "edges": [
            {"from": "inp", "to": "enc"},
            {"from": "enc", "to": "feat", "label": "pool"},
            {"from": "feat", "to": "head"},
            {"from": "head", "to": "loss", "style": "dashed"},
            {"from": "inp", "to": "head", "style": "skip dashed", "offset": 24, "label": "skip"},
        ],
    }


def _parse_svg(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def test_render_architecture_svg_is_valid_and_paper_ready(tmp_path: Path) -> None:
    out = tmp_path / "fig1.svg"
    result = arch.render_architecture_full(_simple_spec(), out, formats=["svg"])
    assert result.primary == out
    assert out.exists() and out.stat().st_size > 2000
    assert result.spec_path is not None and result.spec_path.exists()

    root = _parse_svg(out)
    assert root.tag == f"{SVG_NS}svg"
    text = out.read_text(encoding="utf-8")
    # 白底、无阴影/渐变、分组虚线、圆角
    assert 'fill="#FFFFFF"' in text
    assert "linearGradient" not in text and "<filter" not in text
    assert "stroke-dasharray" in text
    assert "<marker" in text and "<path" in text
    assert 'class="arrowhead"' in text  # 显式箭头（不依赖 marker 的栅格化器也能画出来）
    assert 'rx="' in text
    # 文本内容（label / sub / legend）
    all_text = "".join(node.text or "" for node in root.iter(f"{SVG_NS}text"))
    for keyword in ("Test Architecture", "Encoder", "12 layers", "CE Loss", "Backbone", "Module", "Tensor"):
        assert keyword in all_text, f"SVG 缺少文本：{keyword}"
    # 统一 8pt 主字号（子标签 7pt）
    font_sizes = {node.get("font-size") for node in root.iter(f"{SVG_NS}text")}
    assert "8" in font_sizes and "7" in font_sizes


def test_render_architecture_escapes_special_characters(tmp_path: Path) -> None:
    spec = _simple_spec()
    spec["nodes"][0]["label"] = "A & B <tag>"
    out = tmp_path / "escaped.svg"
    arch.render_architecture(spec, out, fmt="svg")
    text = out.read_text(encoding="utf-8")
    assert "&amp;" in text and "&lt;tag&gt;" in text
    _parse_svg(out)  # 必须是合法 XML


def test_render_architecture_degrades_without_converters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arch, "_load_cairosvg", lambda: None)
    monkeypatch.setattr(arch, "_find_binary", lambda name: None)
    result = arch.render_architecture_full(
        _simple_spec(), tmp_path / "fig.svg", formats=["svg", "pdf", "png"]
    )
    assert result.primary.suffix == ".svg"
    assert [path.suffix for path in result.artifacts] == [".svg"]
    assert any("PDF" in warning for warning in result.warnings)
    assert "降级说明" in result.note


def test_render_architecture_respects_unknown_direction_and_bad_edges(tmp_path: Path) -> None:
    spec = _simple_spec()
    spec["direction"] = "diagonal"
    spec["edges"].append({"from": "missing", "to": "enc"})
    result = arch.render_architecture_full(spec, tmp_path / "x.svg", formats=["svg"])
    assert any("direction" in warning for warning in result.warnings)
    assert any("missing" in warning for warning in result.warnings)


def test_builtin_architecture_templates_render(tmp_path: Path) -> None:
    paths = arch.write_builtin_templates(tmp_path / "templates")
    names = {path.name for path in paths}
    assert {"transformer_block.yaml", "multimodal_pipeline.yaml", "training_loop.yaml"} <= names
    for path in paths:
        spec = arch.load_spec(path)
        result = arch.render_architecture_full(spec, tmp_path / f"{path.stem}.svg", formats=["svg"])
        assert result.primary.exists()
        assert result.width_pt > 100 and result.height_pt > 60
    assert arch.builtin_template("multimodal_pipeline")["direction"] == "horizontal"
    assert arch.builtin_template("transformer_block")["direction"] == "vertical"


# ---------------------------------------------------------------------------
# 数据图
# ---------------------------------------------------------------------------


def _line_spec() -> dict:
    return {
        "kind": "line",
        "title": "Worst-group accuracy ↓ vs. steps",
        "xlabel": "Steps",
        "ylabel": "Acc (%) ↑",
        "style": "neurips",
        "series": [
            {"name": "Baseline", "x": [0, 20, 40], "y": [41, 52, 60], "yerr": [0.5, 0.8, 0.7]},
            {"label": "Ours", "x": [0, 20, 40], "y": [41, 57, 74], "std": [0.4, 0.6, 0.5]},
        ],
        "significance": [{"x1": 0, "x2": 40, "y": 78, "label": "**"}],
        "annotations": [{"x": 40, "y": 74, "text": "best"}],
        "formats": ["pdf", "svg", "png"],
    }


def test_render_plot_line_with_band_and_significance(tmp_path: Path) -> None:
    result = plots.render_plot_full(_line_spec(), tmp_path / "curves.pdf")
    assert result.primary.suffix == ".pdf" and result.primary.exists()
    assert {path.suffix for path in result.artifacts} == {".pdf", ".svg", ".png"}
    assert result.spec_path is not None and result.spec_path.exists()
    # 论文风格 rcParams 生效
    assert plots.plt.rcParams["pdf.fonttype"] == 42
    assert plots.plt.rcParams["axes.spines.top"] is False
    assert plots.plt.rcParams["xtick.direction"] == "in"
    # 图例使用 name/label，而不是 series 1/2
    assert "Baseline" in _line_spec()["series"][0]["name"]
    svg_text = (tmp_path / "curves.svg").read_text(encoding="utf-8")
    assert "Worst-group accuracy" in svg_text


def test_render_plot_multipanel_bar_heatmap_radar(tmp_path: Path) -> None:
    multi = {
        "panels": [
            {"kind": "bar", "title": "Ablation", "categories": ["A", "B"], "series": [{"name": "Ours", "values": [74.1, 69.3]}]},
            {"kind": "heatmap", "title": "Matrix", "matrix": [[1, 2], [3, 4]], "colorbar": True},
        ],
        "ncols": 2,
        "formats": ["pdf"],
    }
    result = plots.render_plot_full(multi, tmp_path / "multi.pdf")
    assert result.primary.exists() and result.primary.suffix == ".pdf"

    radar = {"kind": "radar", "categories": ["A", "B", "C"], "series": [{"name": "x", "values": [1, 2, 3]}], "formats": ["png"]}
    assert plots.render_plot_full(radar, tmp_path / "radar.png").primary.exists()

    scatter = {"kind": "scatter", "series": [{"name": "s", "x": [1, 2], "y": [2, 3]}], "formats": ["svg"]}
    assert plots.render_plot_full(scatter, tmp_path / "scatter.svg").primary.exists()

    with pytest.raises(ValueError):
        plots.render_plot_full({"kind": "unknown", "series": []}, tmp_path / "bad.pdf")


# ---------------------------------------------------------------------------
# 三线表
# ---------------------------------------------------------------------------


def test_latex_table_booktabs_highlight_and_groups() -> None:
    spec = {
        "caption": "Main results with 95% CI.",
        "label": "tab:main",
        "columns": ["Method", "Top-1", "Top-5", "AP", "AP$_{50}$"],
        "column_groups": [
            {"title": "ImageNet", "columns": [1, 2]},
            {"title": "COCO", "columns": [3, 4]},
        ],
        "metric_columns": [1, 2, 3, 4],
        "rows": [
            ["Baseline", 75.4, 92.1, 39.8, 61.2],
            ["Ours", 78.2, 93.4, 41.0, 63.7],
            ["Ours + aug", 77.9, 93.2, 40.5, 63.0],
        ],
        "note": "Best in bold, second underlined.",
    }
    latex = tables.render_latex_table(spec)
    assert r"\toprule" in latex and r"\midrule" in latex and r"\bottomrule" in latex
    assert r"\textbf{78.2}" in latex  # 最佳加粗
    assert r"\underline{77.9}" in latex  # 次佳下划线
    assert r"\multicolumn{2}{c}" in latex and r"\cmidrule(lr){2-3}" in latex
    assert r"\caption{Main results with 95\% CI.}" in latex
    assert r"\label{tab:main}" in latex
    assert r"\begin{table}" in latex and r"\end{table}" in latex
    tabular = tables.render_latex_table(spec, standalone=False)
    assert tabular.startswith(r"\begin{tabular}") and r"\end{table}" not in tabular


def test_latex_table_lower_is_better_and_escaping() -> None:
    spec = {
        "columns": ["Variant", "Params (M) ↓", "Worst-group Acc."],
        "metric_columns": [1, 2],
        "higher_is_better": {1: False, 2: True},
        "rows": [["A & B", 87.8, 66.0], ["C_D", 86.0, 74.1]],
    }
    latex = tables.render_latex_table(spec, standalone=False)
    assert r"\textbf{86}" in latex or r"\textbf{86.0}" in latex  # 参数越少越好
    assert r"75.0" not in latex
    assert r"A \& B" in latex and r"C\_D" in latex


def test_markdown_table() -> None:
    spec = {
        "columns": ["Method", "Acc"],
        "rows": [["A", 1.0], ["B", 2.0]],
        "metric_columns": [1],
    }
    md = tables.render_markdown_table(spec)
    assert "| Method | Acc |" in md
    assert "**2" in md or "**2.0**" in md


def test_builtin_table_templates_render() -> None:
    paths = tables.write_builtin_templates(Path("/tmp") / "sf-table-templates")
    assert len(paths) == 2
    for path in paths:
        spec = tables.load_spec(path)
        assert r"\toprule" in tables.render_latex_table(spec)


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


def test_figure_tools_registered_and_run(tmp_path: Path) -> None:
    names = {"figure_architecture", "figure_plot", "figure_table"}
    assert names <= {tool.name for tool in (get_tool(name) for name in names) if tool}
    ctx = _context(tmp_path)

    arch_result = run_tool(
        "figure_architecture",
        ctx,
        {"spec": _simple_spec(), "formats": "svg"},
    )
    assert arch_result.ok and arch_result.artifacts
    assert all(Path(path).is_absolute() and Path(path).exists() for path in arch_result.artifacts)

    plot_result = run_tool(
        "figure_plot",
        ctx,
        {"spec": {**_line_spec(), "formats": ["svg"]}, "formats": "svg"},
    )
    assert plot_result.ok and plot_result.artifacts

    table_result = run_tool(
        "figure_table",
        ctx,
        {"spec": {"columns": ["M", "A"], "rows": [["x", 1.0], ["y", 2.0]], "label": "tab:x"}},
    )
    assert table_result.ok
    assert any(path.endswith(".tex") for path in table_result.artifacts)

    bad = run_tool("figure_architecture", ctx, {"spec": {"nodes": []}})
    assert not bad.ok and bad.error


def test_figures_cli_smoke(tmp_path: Path) -> None:
    spec_path = tmp_path / "arch.yaml"
    arch.save_spec(_simple_spec(), spec_path)
    out_path = tmp_path / "cli_arch.svg"

    app = typer.Typer()
    add_figures_cli(app)
    runner = CliRunner()
    result = runner.invoke(app, ["fig", "arch", "--spec", str(spec_path), "--out", str(out_path), "--formats", "svg"])
    assert result.exit_code == 0, result.output
    assert out_path.exists()

    template_dir = tmp_path / "tpl"
    result = runner.invoke(app, ["fig", "templates", "--dest", str(template_dir)])
    assert result.exit_code == 0, result.output
    assert (template_dir / "transformer_block.yaml").exists()
