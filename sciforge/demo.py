"""离线演示：无需 API key、无需 GPU，跑通从文献到论文骨架的完整链路。

``sciforge demo`` 会创建一个演示项目并产出：
- `.sciforge/runs/<run_id>/REPORT.md` — 流水线报告（Mock LLM 生成结构化内容）
- `figures/fig1.svg` — 顶会风格模型架构图（若 figures 模块就绪）
- `paper/` — LaTeX 论文工程骨架（若 writing 模块就绪）
- `notes/` — 演示用主题/文献笔记
- `README.md` — 演示说明
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import sciforge
from sciforge.config import ensure_dirs, get_settings, save_project_config

DEMO_ARCH_SPEC: dict[str, Any] = {
    "title": "Adaptive Compute Routing for Long-Tail Vision (Demo)",
    "width": 1160,
    "height": 380,
    "groups": [
        {"id": "input", "label": "Input & Backbone", "row": 0, "col": 0, "span": 2},
        {"id": "core", "label": "Adaptive Routing (Ours)", "row": 0, "col": 2, "span": 3},
        {"id": "out", "label": "Prediction & Loss", "row": 0, "col": 5, "span": 2},
    ],
    "nodes": [
        {"id": "img", "label": "Input Image", "sub": "H×W×3", "kind": "data", "group": "input"},
        {"id": "bb", "label": "Backbone", "sub": "ViT-B/16 · 12 layers", "kind": "module", "group": "input"},
        {
            "id": "unc",
            "label": "Uncertainty Head",
            "sub": "entropy · confidence",
            "kind": "module",
            "group": "core",
        },
        {"id": "route", "label": "Soft Router", "sub": "top-k · load balance", "kind": "op", "group": "core"},
        {"id": "expr", "label": "Expert Pool", "sub": "4 experts · shared", "kind": "module", "group": "core"},
        {"id": "head", "label": "Task Head", "sub": "class logits", "kind": "module", "group": "out"},
        {
            "id": "loss",
            "label": "Losses",
            "sub": "CE + balance + consistency",
            "kind": "loss",
            "group": "out",
        },
    ],
    "edges": [
        {"from": "img", "to": "bb"},
        {"from": "bb", "to": "unc"},
        {"from": "unc", "to": "route"},
        {"from": "route", "to": "expr", "label": "route weights"},
        {"from": "expr", "to": "head"},
        {"from": "unc", "to": "loss", "style": "dashed", "label": "reg"},
        {"from": "head", "to": "loss"},
    ],
}

DEMO_PLOT_SPEC: dict[str, Any] = {
    "kind": "line",
    "title": "Worst-group accuracy vs. training steps",
    "xlabel": "Training steps",
    "ylabel": "Worst-group accuracy (%)",
    "series": [
        {"label": "Baseline", "x": [0, 20, 40, 60, 80, 100], "y": [41.0, 52.4, 59.8, 63.5, 65.1, 66.0]},
        {"label": "Ours", "x": [0, 20, 40, 60, 80, 100], "y": [41.2, 56.8, 65.4, 70.9, 73.2, 74.1], "std": [0, 1.2, 1.0, 0.9, 0.8, 0.7]},
        {"label": "Ours w/o router", "x": [0, 20, 40, 60, 80, 100], "y": [41.0, 54.1, 62.2, 66.8, 68.5, 69.3], "std": [0, 1.4, 1.2, 1.0, 0.9, 0.8]},
    ],
    "style": "neurips",
}

DEMO_RESULTS: dict[str, Any] = {
    "dataset": "ImageNet-LT + ImageNet-R (smoke)",
    "baseline": {"worst_group_acc": 66.0, "avg_acc": 78.4, "params_m": 86.0, "tflops": 17.6},
    "ours": {"worst_group_acc": 74.1, "avg_acc": 79.6, "params_m": 87.8, "tflops": 17.9},
    "ablation": [
        {"name": "Ours", "worst_group_acc": 74.1},
        {"name": "w/o router", "worst_group_acc": 69.3},
        {"name": "w/o consistency", "worst_group_acc": 71.6},
    ],
}

_README = """# SciForge 离线演示项目

本项目由 `sciforge demo` 生成，用于在不配置 API key / GPU 服务器的情况下体验完整科研链路。

## 已生成内容

- `.sciforge/runs/<run_id>/REPORT.md` — 流水线报告（Mock LLM）
- `figures/fig1.svg` — 论文级模型架构图（如 figures 模块就绪）
- `paper/` — LaTeX 论文工程骨架（如 writing 模块就绪）
- `notes/` — 主题与演示数据
- `data/demo/` — 演示用 fixtures

## 切换真实模式

```bash
export DEEPSEEK_API_KEY=sk-...
sciforge pipeline run --recipe survey --topic "你的研究主题"
sciforge chat "帮我调研 …"
```
"""


def _copy_fixtures(dest: Path) -> dict[str, Any]:
    """复制 examples/demo/fixtures 中的素材（若存在），返回信息。"""
    info: dict[str, Any] = {"found": False, "files": []}
    repo_root = Path(__file__).resolve().parents[1]
    fixtures = repo_root / "examples" / "demo" / "fixtures"
    target = dest / "data" / "demo"
    target.mkdir(parents=True, exist_ok=True)
    if not fixtures.exists():
        return info
    info["found"] = True
    for item in fixtures.rglob("*"):
        if item.is_file():
            relative = item.relative_to(fixtures)
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(item, out)
                info["files"].append(str(out))
            except OSError:
                continue
    return info


def _seed_library(dest: Path, info: dict[str, Any]) -> int:
    """把 fixtures 中的文献写入本地文献库（若 library 模块就绪）。"""
    added = 0
    try:
        from sciforge.library import PaperLibrary, PaperRecord

        library = PaperLibrary(dest.parent / ".sciforge-demo-data" if False else Path.home() / ".sciforge")
        json_files = list((dest / "data" / "demo").glob("*.json"))
        for json_file in json_files:
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            items = (payload.get("papers") or payload.get("results")) if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                record = PaperRecord(
                    id=str(item.get("id") or item.get("arxiv_id") or ""),
                    title=str(item.get("title") or ""),
                    authors=[str(a) for a in item.get("authors") or []],
                    abstract=str(item.get("summary") or item.get("abstract") or ""),
                    url=str(item.get("abs") or item.get("url") or ""),
                    pdf_url=str(item.get("pdf") or item.get("pdf_url") or ""),
                    categories=[str(c) for c in item.get("categories") or []],
                    summary=str(item.get("summary") or ""),
                    source=str(item.get("source") or "demo"),
                )
                if record.id and record.title:
                    library.add(record)
                    added += 1
    except Exception:  # noqa: BLE001
        return 0
    return added


def _render_figure(dest: Path) -> dict[str, Any]:
    """渲染模型架构图；失败时保底写出 spec。"""
    spec_path = dest / "figures" / "fig1.yaml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        spec_path.write_text(yaml.safe_dump(DEMO_ARCH_SPEC, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        spec_path.write_text(json.dumps(DEMO_ARCH_SPEC, ensure_ascii=False, indent=2), encoding="utf-8")

    result: dict[str, Any] = {"spec": str(spec_path), "svg": None, "error": None}
    try:
        from sciforge.figures.arch import render_architecture

        out = dest / "figures" / "fig1.svg"
        render_architecture(DEMO_ARCH_SPEC, out)
        result["svg"] = str(out)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _render_plot(dest: Path) -> dict[str, Any]:
    spec_path = dest / "figures" / "curves.yaml"
    result: dict[str, Any] = {"spec": str(spec_path), "file": None, "error": None}
    try:
        import yaml

        spec_path.write_text(yaml.safe_dump(DEMO_PLOT_SPEC, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        return result
    try:
        from sciforge.figures.plots import render_plot

        out = dest / "figures" / "curves.pdf"
        render_plot(DEMO_PLOT_SPEC, out)
        result["file"] = str(out)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _create_paper(dest: Path, topic: str) -> dict[str, Any]:
    result: dict[str, Any] = {"dir": None, "error": None}
    paper_dir = dest / "paper"
    try:
        from sciforge.writing.paper import create_paper_project

        create_paper_project(
            paper_dir,
            venue="generic",
            title=topic,
            authors=["SciForge Demo"],
        )
        result["dir"] = str(paper_dir)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    # 兜底：手写最小可编译 LaTeX 工程
    try:
        paper_dir.mkdir(parents=True, exist_ok=True)
        (paper_dir / "main.tex").write_text(
            "\\documentclass[11pt]{article}\n"
            "\\usepackage[margin=1in]{geometry}\n\\usepackage{booktabs}\n\\usepackage{graphicx}\n"
            f"\\title{{{topic}}}\n\\author{{SciForge Demo}}\n\\date{{\\today}}\n"
            "\\begin{document}\n\\maketitle\n"
            "\\begin{abstract}\n"
            "本文演示 SciForge 如何从文献调研、创新点挖掘、实验设计一路生成论文骨架。"
            "以下章节为占位结构，接入真实 LLM 与实验结果后会被填充。\n"
            "\\end{abstract}\n\n"
            "\\section{Introduction}\n（待写）\n\n"
            "\\section{Related Work}\n（待写）\n\n"
            "\\section{Method}\n（待写）\n\n"
            "\\section{Experiments}\n（待写）\n\n"
            "\\section{Conclusion}\n（待写）\n\n"
            "\\bibliographystyle{plain}\n\\bibliography{refs}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        (paper_dir / "refs.bib").write_text("% SciForge demo references\n", encoding="utf-8")
        (paper_dir / "README.md").write_text(
            "# 论文工程\n\n编译：`latexmk -pdf main.tex`（需 TeX Live）。\n", encoding="utf-8"
        )
        result["dir"] = str(paper_dir)
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _tree(path: Path, depth: int = 2) -> str:
    lines = [f"{path.name}/"]
    ignore = {".git", "__pycache__", ".venv"}
    for item in sorted(path.rglob("*")):
        if any(part in ignore for part in item.parts):
            continue
        rel = item.relative_to(path)
        if len(rel.parts) > depth:
            continue
        indent = "  " * (len(rel.parts) - 1)
        lines.append(f"{indent}{'└─ ' if item.is_file() else '├─ '}{item.name}{'' if item.is_file() else '/'}")
    return "\n".join(lines[:60])


def run_demo(dest: Path, *, recipe: str = "survey", topic: str = "面向长尾与分布偏移的视觉模型自适应计算") -> dict[str, Any]:
    dest = Path(dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for sub in ("notes", "experiments", "results", "figures", "paper", "refs", "data", ".sciforge"):
        (dest / sub).mkdir(parents=True, exist_ok=True)

    settings = get_settings(dest)
    settings.project_name = dest.name
    save_project_config(dest, settings)
    ensure_dirs(settings)

    # 演示素材
    fixtures = _copy_fixtures(dest)
    (dest / "notes" / "topic.md").write_text(f"# 研究主题\n\n{topic}\n", encoding="utf-8")
    (dest / "results" / "metrics.json").write_text(
        json.dumps(DEMO_RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    library_count = _seed_library(dest, fixtures)

    # 1) 离线 pipeline
    from sciforge.agent.runtime import make_bus
    from sciforge.pipeline.engine import PipelineRunner

    bus = make_bus()
    runner = PipelineRunner(settings, dest, mock=True, bus=bus)
    run = runner.run(recipe, topic)

    # 2) 图 / 论文 / 说明
    arch = _render_figure(dest)
    curve = _render_plot(dest)
    paper = _create_paper(dest, topic)
    (dest / "README.md").write_text(_README, encoding="utf-8")

    # 3) 把关键产物汇总成一份易读的总结
    report_path = run.run_dir / "REPORT.md"
    artifacts: list[str] = [str(report_path)]
    for item in (arch.get("svg"), curve.get("file"), paper.get("dir")):
        if item:
            artifacts.append(str(item))

    lines = [
        "# SciForge 离线演示完成 ✓",
        "",
        f"- 演示项目：`{dest}`",
        f"- 流水线：`{run.recipe}` · 状态 **{run.status}** · {len(run.stages)} 个阶段",
        f"- 报告：`{report_path}`",
        f"- 文献库：新增 {library_count} 篇（fixtures {len(fixtures['files'])} 个文件）",
        "",
        "## 生成的产物",
        "",
        f"- 模型架构图：{arch.get('svg') or '（figures 模块未就绪，spec 已写入 ' + arch['spec'] + '）'}",
        f"- 实验曲线：{curve.get('file') or '（plots 模块未就绪，spec 已写入 ' + curve['spec'] + '）'}",
        f"- 论文工程：{paper.get('dir') or '（writing 模块未就绪）'}",
        "",
        "## 项目结构",
        "```",
        _tree(dest),
        "```",
        "",
        "## 下一步（切换真实模式）",
        "",
        "```bash",
        "export DEEPSEEK_API_KEY=sk-...",
        f"cd {dest}",
        "sciforge doctor",
        'sciforge pipeline run --recipe full --topic "你的研究主题"',
        "sciforge serve   # 本地 Web 控制台",
        "```",
        "",
        f"> SciForge v{sciforge.__version__} · 离线 Mock 模式生成",
    ]
    markdown = "\n".join(lines)
    (dest / "DEMO.md").write_text(markdown, encoding="utf-8")

    return {
        "dest": dest,
        "run": run.to_dict(),
        "markdown": markdown,
        "artifacts": artifacts,
        "fixtures": fixtures,
        "library_count": library_count,
        "figures": {"arch": arch, "plot": curve},
        "paper": paper,
    }


__all__ = ["run_demo", "DEMO_ARCH_SPEC", "DEMO_PLOT_SPEC", "DEMO_RESULTS"]
