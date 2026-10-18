"""审稿式自检：规则检查为主，LLM 审稿为辅（可选、不强制依赖）。

规则部分不联网、不需要 API key，覆盖审稿人最常提的问题：
- 结构完整性与引用完整性（复用 :meth:`PaperProject.check`）；
- TODO/占位符、摘要长度、实验数字、消融、局限、可复现性声明；
- 图/表/引用数量与正文篇幅。

有 LLM（且配置了 API key）时，额外用 reviewer 角色生成「Major/Minor 意见」；
LLM 调用失败只降级，不影响规则报告的产出。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sciforge.writing.paper import PaperProject

_LLM_SYSTEM = (
    "你是一位顶会（NeurIPS/CVPR/ICLR/ACL）资深审稿人。请用严格但建设性的语气审稿。"
    "只输出 Markdown：`## Major Issues` 与 `## Minor Issues` 两节，每条 1-2 句，"
    "引用具体章节/实验；不要编造实验数字。"
)


def _severity_rank(severity: str) -> int:
    return {"major": 0, "minor": 1, "info": 2}.get(severity, 3)


def _issue(severity: str, kind: str, message: str, *, file: str = "") -> dict[str, Any]:
    return {"severity": severity, "kind": kind, "message": message, "file": file}


def _project(paper_dir: str | Path) -> PaperProject:
    return PaperProject(paper_dir)


def _collect_text(project: PaperProject) -> str:
    texts = []
    section_files = (
        sorted(project.sections_dir.glob("*.tex")) if project.sections_dir.exists() else []
    )
    for path in [project.main_tex, *section_files]:
        if path.exists():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(texts)


def _count_words(text: str) -> int:
    from sciforge.writing.paper import _word_count

    return _word_count(text)


def rule_review(paper_dir: str | Path, *, results_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """纯规则审稿，返回 issue 列表（severity: major|minor|info）。"""
    project = _project(paper_dir)
    if not project.exists:
        return [_issue("major", "missing_project", f"找不到论文工程（缺少 main.tex）：{project.dest}")]
    issues: list[dict[str, Any]] = []
    checks = project.check()
    error_kinds = {
        "undefined_reference",
        "undefined_citation",
        "missing_figure",
        "missing_input",
        "missing_bibliography",
        "broken_document",
        "missing_abstract",
    }
    for check_issue in checks:
        severity = check_issue.get("severity")
        kind = check_issue.get("kind", "")
        if severity == "error":
            bucket = "major" if kind in error_kinds or kind == "missing_main" else "minor"
            issues.append(
                _issue(bucket, f"check:{kind}", check_issue.get("message", ""), file=check_issue.get("file", ""))
            )
        elif severity == "warning" and kind in (
            "abstract_too_long",
            "missing_section",
            "placeholder_title",
        ):
            issues.append(
                _issue("minor", f"check:{kind}", check_issue.get("message", ""), file=check_issue.get("file", ""))
            )

    stats = project.stats()
    text = _collect_text(project)
    lowered = text.lower()

    # TODO / 占位符
    todos = stats.get("todos", 0)
    if todos > 15:
        issues.append(_issue("major", "too_many_todos", f"草稿仍有 {todos} 处 TODO/占位符，远未到可投稿状态"))
    elif todos > 0:
        issues.append(_issue("minor", "todos", f"草稿仍有 {todos} 处 TODO/占位符需要替换"))

    # 篇幅
    total_words = int(stats.get("total_words", 0))
    if total_words < 600:
        issues.append(
            _issue("major", "too_short", f"全文约 {total_words} 词（去命令后估算），正文明显偏短，主张难以成立")
        )
    elif total_words < 1500:
        issues.append(_issue("minor", "short", f"全文约 {total_words} 词，建议补充方法细节与实验分析"))

    # 图/表/引用
    if stats.get("figures", 0) == 0:
        issues.append(
            _issue("major", "no_figure", "论文没有任何插图：顶会论文至少需要一张方法总览图 + 一张结果图")
        )
    if stats.get("tables", 0) == 0:
        issues.append(_issue("major", "no_table", "论文没有表格：主结果/消融应使用 booktabs 三线表"))
    citations = int(stats.get("citations", 0))
    if citations < 5:
        issues.append(_issue("major", "few_citations", f"正文只有 {citations} 处引用，related work 明显不足"))

    # 实验与分析
    if "ablation" not in lowered and "消融" not in lowered:
        issues.append(_issue("minor", "no_ablation", "缺少消融实验（审稿人几乎必问每个组件的贡献）"))
    if not re.search(r"\bseed(s)?\b|随机种子|reproduc", lowered):
        issues.append(_issue("minor", "no_reproducibility", "缺少随机种子/复现性声明（seed、环境、硬件）"))
    if not re.search(r"limitation|局限", lowered):
        issues.append(_issue("minor", "no_limitations", "缺少 Limitations 讨论（诚实说明局限是加分项）"))
    numeric = re.findall(r"\b\d+(?:\.\d+)?\b", text)
    if len(numeric) < 10:
        issues.append(_issue("major", "no_numbers", "正文几乎没有具体数字：每个论断都应有可追溯的指标"))

    # 摘要长度
    abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.S)
    if abstract:
        words = _count_words(abstract.group(1))
        if words > 250:
            issues.append(_issue("major", "abstract_long", f"摘要约 {words} 词，超过 250 词上限"))
        elif words < 80:
            issues.append(_issue("minor", "abstract_short", f"摘要约 {words} 词，建议写满 120–200 词"))
    else:
        issues.append(_issue("major", "abstract_missing", "缺少 abstract 环境"))

    # 结果文件与正文数字一致性（若提供 results_dir）
    if results_dir:
        metrics_path = Path(results_dir) / "metrics.json"
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                metrics = None
            values = _flatten_numbers(metrics) if isinstance(metrics, dict) else []
            if values:
                missing = [value for value in values if f"{value:g}" not in text][:3]
                if missing:
                    issues.append(
                        _issue(
                            "minor",
                            "metrics_mismatch",
                            "results/metrics.json 中的数字（如 "
                            + ", ".join(f"{value:g}" for value in missing)
                            + "）未在正文出现，请确认实验数字可追溯",
                        )
                    )
    issues.sort(key=lambda item: (_severity_rank(item["severity"]), item["kind"]))
    return issues


def _flatten_numbers(payload: Any, *, limit: int = 20) -> list[float]:
    numbers: list[float] = []

    def walk(node: Any) -> None:
        if len(numbers) >= limit:
            return
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            numbers.append(float(node))

    walk(payload)
    return numbers


def _try_llm() -> tuple[Any | None, str]:
    """尝试构造 LLM router；不可用时返回 (None, 原因)。"""
    try:
        from sciforge.config import get_settings
        from sciforge.llm.router import LLMRouter

        settings = get_settings()
        provider_name = settings.llm.provider_name_for("reviewer")
        if not settings.resolve_api_key(provider_name):
            return None, "未配置 API key（可设置 DEEPSEEK_API_KEY 后重试）"
        router = LLMRouter(settings)
        if router.force_mock:
            return None, "当前为 Mock 模式（SCIFORGE_MOCK=1）"
        return router, ""
    except Exception as exc:  # noqa: BLE001 - 自检不能因 LLM 配置失败而中断
        return None, f"{type(exc).__name__}: {exc}"


def _llm_review(project: PaperProject, llm: Any, issues: list[dict[str, Any]], venue: str) -> tuple[str | None, str]:
    stats = project.stats()
    check_summary = "\n".join(
        f"- [{issue['severity']}] {issue['message']}" for issue in issues[:25]
    )
    prompt = (
        f"会议/期刊：{venue}\n"
        f"标题：{project.meta.get('title', '')}\n"
        f"字数估算：{stats.get('total_words', 0)}；引用：{stats.get('citations', 0)}；"
        f"图：{stats.get('figures', 0)}；表：{stats.get('tables', 0)}\n\n"
        f"规则检查发现的问题：\n{check_summary}\n\n"
        "请基于以上信息与论文文件（你是审稿人，可以要求作者补充内容）给出 3-5 条 Major、"
        "4-6 条 Minor 意见。不要编造论文中不存在的结果。"
    )
    try:
        text = llm.complete("reviewer", prompt, system=_LLM_SYSTEM)
        return str(text).strip(), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"LLM 审稿调用失败：{type(exc).__name__}: {exc}"


def self_review(
    paper_dir: str | Path,
    *,
    llm: Any | None = None,
    results_dir: str | Path | None = None,
    venue: str | None = None,
    use_llm: bool = True,
) -> str:
    """生成 Markdown 审稿式自检报告（规则检查 + 可选 LLM）。"""
    project = _project(paper_dir)
    stats = project.stats() if project.exists else {}
    issues = rule_review(paper_dir, results_dir=results_dir)
    major = [issue for issue in issues if issue["severity"] == "major"]
    minor = [issue for issue in issues if issue["severity"] == "minor"]
    venue_name = venue or stats.get("venue") or project.venue or "generic"
    title = project.meta.get("title") or "（未命名）"

    decision = (
        "❌ 尚不可投稿：先解决 Major 问题"
        if len(major) >= 3
        else ("⚠️ 接近草稿完成：Major 问题清理后可送内部审阅" if major else "✅ 结构完整：可进入细节打磨/语言润色")
    )
    lines = [
        f"# 审稿式自检报告：{title}",
        "",
        f"- 目标 venue：**{venue_name}**",
        f"- 工程目录：`{project.dest}`",
        f"- 估算篇幅：{stats.get('total_words', 0)} 词；引用 {stats.get('citations', 0)} 处；"
        f"图 {stats.get('figures', 0)} 张；表 {stats.get('tables', 0)} 个；"
        f"TODO {stats.get('todos', 0)} 处",
        f"- 总体判断：{decision}",
        "",
        f"## Major Issues（{len(major)}）",
        "",
    ]
    if major:
        lines += [f"{index}. **[{issue['kind']}]** {issue['message']}" for index, issue in enumerate(major, 1)]
    else:
        lines.append("（无）")
    lines += ["", f"## Minor Issues（{len(minor)}）", ""]
    if minor:
        lines += [f"{index}. [{issue['kind']}] {issue['message']}" for index, issue in enumerate(minor, 1)]
    else:
        lines.append("（无）")

    lines += [
        "",
        "## 下一步建议",
        "",
        "1. 用 `sciforge write check` 看逐文件、逐行的静态问题。",
        "2. 把 TODO 换成真实内容，所有数字追溯 `results/`。",
        "3. 图表用 `sciforge fig arch/plot/table` 生成，确认每张图在正文被 `\\ref`。",
        "4. 投稿前跑 `sciforge write compile` 确认 0 error。",
        "",
    ]

    if not use_llm:
        lines += ["> 规则版报告（未启用 LLM 审稿：use_llm=false）", ""]
    if use_llm and project.exists:
        router = llm
        reason = ""
        if router is None:
            router, reason = _try_llm()
        if router is not None:
            text, error = _llm_review(project, router, issues, venue_name)
            if text:
                lines += ["## LLM 审稿意见（reviewer 角色）", "", text, ""]
            else:
                lines += [f"> LLM 审稿不可用：{error}", ""]
        else:
            lines += [f"> 规则版报告（未启用 LLM 审稿：{reason}）", ""]

    return "\n".join(lines)


__all__ = ["rule_review", "self_review"]
