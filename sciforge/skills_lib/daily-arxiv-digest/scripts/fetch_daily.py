#!/usr/bin/env python
"""拉取 daily-arXiv-ai-enhanced 的每日 AI 增强摘要（纯标准库，可选缓存）。

数据源::

    https://raw.githubusercontent.com/dw-dengwei/daily-arXiv-ai-enhanced/main/data/file-list.txt
    https://raw.githubusercontent.com/dw-dengwei/daily-arXiv-ai-enhanced/main/data/<date>_AI_enhanced_Chinese.jsonl

用法::

    python fetch_daily.py --category cs.CV --limit 20
    python fetch_daily.py --date 2025-01-15 --keywords "diffusion,video" --json
    python fetch_daily.py --offline --cache data/daily_cache.jsonl   # 只读本地缓存

注意：非官方 API 可能变更；失败时请走 sciforge `lit_daily` 工具（有降级逻辑）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAW_BASE = "https://raw.githubusercontent.com/dw-dengwei/daily-arXiv-ai-enhanced/main/data"
FILE_LIST_URL = f"{RAW_BASE}/file-list.txt"
DEFAULT_CACHE = Path.home() / ".cache" / "sciforge" / "daily_arxiv_cache.jsonl"
TIMEOUT = 30


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "SciForge/0.1 (+research)"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - 固定可信主机
        return response.read().decode("utf-8", errors="replace")


def latest_date() -> str | None:
    """从 file-list.txt 找最新日期（文件名形如 2025-01-15_AI_enhanced_Chinese.jsonl）。"""
    try:
        listing = _get(FILE_LIST_URL)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    dates = []
    for line in listing.splitlines():
        line = line.strip()
        if "_AI_enhanced_Chinese.jsonl" not in line:
            continue
        name = line.split("/")[-1]
        date = name.split("_", 1)[0]
        if len(date) == 10:
            dates.append(date)
    return max(dates) if dates else None


def fetch(date: str) -> list[dict]:
    url = f"{RAW_BASE}/{date}_AI_enhanced_Chinese.jsonl"
    try:
        raw = _get(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[错误] 拉取失败 {url}: {exc}", file=sys.stderr)
        return []
    papers = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            papers.append(item)
    return papers


def filter_papers(papers: list[dict], *, category: str | None, keywords: list[str], limit: int) -> list[dict]:
    selected = []
    for paper in papers:
        categories = paper.get("categories") or paper.get("category") or []
        if isinstance(categories, str):
            categories = [categories]
        if category and not any(str(item).startswith(category) for item in categories):
            continue
        if keywords:
            haystack = " ".join(
                str(paper.get(key, "")) for key in ("title", "summary", "abstract", "tldr", "authors")
            ).lower()
            if not any(keyword.lower() in haystack for keyword in keywords):
                continue
        selected.append(paper)
        if len(selected) >= limit:
            break
    return selected


def to_markdown(papers: list[dict], date: str) -> str:
    lines = [f"# arXiv 每日速览 {date}", f"共 {len(papers)} 篇", ""]
    for index, paper in enumerate(papers, start=1):
        title = paper.get("title") or "(无标题)"
        link = paper.get("url") or paper.get("arxiv_url") or ""
        summary = paper.get("summary") or paper.get("tldr") or paper.get("abstract") or ""
        lines.append(f"{index}. **{title.strip()}**  ")
        lines.append(f"   {summary.strip()[:240]}  ")
        if link:
            lines.append(f"   {link}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="拉取 daily-arXiv-ai-enhanced 每日摘要")
    parser.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认最新")
    parser.add_argument("--category", default=None, help="arXiv 类别前缀，如 cs.CV")
    parser.add_argument("--keywords", default="", help="逗号分隔关键词（标题/摘要匹配）")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--markdown", action="store_true", help="输出 Markdown 简报")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="提交缓存文件")
    args = parser.parse_args(argv)

    date = args.date or latest_date()
    if not date:
        print("[错误] 无法确定最新日期（网络不可用或数据源变更）。请用 sciforge lit_daily 或指定 --date。", file=sys.stderr)
        return 1
    papers = fetch(date)
    if not papers:
        return 1
    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    selected = filter_papers(papers, category=args.category, keywords=keywords, limit=args.limit)

    args.cache.parent.mkdir(parents=True, exist_ok=True)
    with args.cache.open("a", encoding="utf-8") as handle:
        for paper in selected:
            record = {"date": date, "id": paper.get("id"), "title": paper.get("title")}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if args.json:
        print(json.dumps({"date": date, "count": len(selected), "papers": selected}, ensure_ascii=False, indent=2))
    elif args.markdown:
        print(to_markdown(selected, date))
    else:
        for index, paper in enumerate(selected, start=1):
            print(f"{index}. {paper.get('title', '')}")
            print(f"   {(paper.get('summary') or paper.get('tldr') or '')[:160]}")
            print(f"   {paper.get('url', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
