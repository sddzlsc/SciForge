"""图配色/样式出口：架构图、数据图、三线表。

各子模块可以独立导入；这里只做便捷再导出，避免上层写长路径。
"""

from __future__ import annotations

from sciforge.figures import arch, plots, tables

OKABE_ITO = arch.OKABE_ITO
PALETTE = plots.OKABE_ITO
render_architecture = arch.render_architecture
render_architecture_full = arch.render_architecture_full
render_plot = plots.render_plot
render_plot_full = plots.render_plot_full
render_latex_table = tables.render_latex_table
render_markdown_table = tables.render_markdown_table

__all__ = [
    "arch",
    "plots",
    "tables",
    "OKABE_ITO",
    "PALETTE",
    "render_architecture",
    "render_architecture_full",
    "render_plot",
    "render_plot_full",
    "render_latex_table",
    "render_markdown_table",
]
