#!/usr/bin/env python
"""批量查询 DOI 的开放获取（OA）状态（Unpaywall + OpenAlex，纯标准库）。

用法::

    python oa_lookup.py 10.1109/CVPR.2024.00123
    python oa_lookup.py dois.txt --json -o oa_status.json
    python oa_lookup.py dois.txt --email you@example.com --sleep 0.3

说明：公共 API 需要文明使用；建议提供 email（Unpaywall 礼貌池），失败自动降级到 OpenAlex。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UNPAYWALL = "https://api.unpaywall.org/v2/{doi}?email={email}"
OPENALEX = "https://api.openalex.org/works/https://doi.org/{doi}"
TIMEOUT = 20
DEFAULT_EMAIL = "sciforge@example.com"


def _get_json(url: str) -> dict | None:
    request = urllib.request.Request(url, headers={"User-Agent": "SciForge/0.1 (oa lookup)"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - 固定可信主机
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def lookup_unpaywall(doi: str, email: str) -> dict | None:
    payload = _get_json(UNPAYWALL.format(doi=urllib.parse.quote(doi, safe=""), email=urllib.parse.quote(email)))
    if not payload:
        return None
    locations = payload.get("oa_locations") or []
    pdf_links = [item.get("url_for_pdf") or item.get("url") for item in locations if item.get("url_for_pdf") or item.get("url")]
    return {
        "doi": doi,
        "source": "unpaywall",
        "is_oa": bool(payload.get("is_oa")),
        "oa_status": payload.get("oa_status"),
        "journal": payload.get("journal_name"),
        "year": payload.get("year"),
        "title": payload.get("title"),
        "best_pdf": (payload.get("best_oa_location") or {}).get("url_for_pdf"),
        "pdf_links": [link for link in pdf_links if link][:5],
        "license": (payload.get("best_oa_location") or {}).get("license"),
    }


def lookup_openalex(doi: str) -> dict | None:
    payload = _get_json(OPENALEX.format(doi=urllib.parse.quote(doi, safe="")))
    if not payload:
        return None
    locations = payload.get("locations") or []
    pdf_links = []
    for location in locations:
        pdf_url = location.get("pdf_url")
        if pdf_url and pdf_url not in pdf_links:
            pdf_links.append(pdf_url)
    return {
        "doi": doi,
        "source": "openalex",
        "is_oa": bool(payload.get("open_access", {}).get("is_oa")),
        "oa_status": payload.get("open_access", {}).get("oa_status"),
        "year": payload.get("publication_year"),
        "title": payload.get("title"),
        "best_pdf": pdf_links[0] if pdf_links else None,
        "pdf_links": pdf_links[:5],
        "license": None,
    }


def lookup(doi: str, *, email: str, fallback: bool = True) -> dict:
    result = lookup_unpaywall(doi, email)
    if fallback and (result is None or not result.get("is_oa")):
        alternative = lookup_openalex(doi)
        if alternative and (result is None or alternative.get("is_oa")):
            result = alternative
    if result is None:
        return {"doi": doi, "source": "none", "is_oa": False, "pdf_links": [], "error": "查询失败（网络/限流/DOI 无效）"}
    return result


def _load_dois(arg: str) -> list[str]:
    path = Path(arg)
    if path.exists() and path.is_file():
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    else:
        lines = [arg]
    dois = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if "doi.org/" in line:
            line = line.split("doi.org/", 1)[1]
        dois.append(line.strip())
    return dois


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量查询 DOI 的 OA 状态与 PDF 链接")
    parser.add_argument("input", help="单个 DOI 或 DOI 列表文件（每行一个）")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="Unpaywall 礼貌 email")
    parser.add_argument("--sleep", type=float, default=0.3, help="每次请求间隔秒数")
    parser.add_argument("--no-fallback", action="store_true", help="不使用 OpenAlex 兜底")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("-o", "--output", type=Path, default=None, help="写入结果文件")
    args = parser.parse_args(argv)

    dois = _load_dois(args.input)
    if not dois:
        print("[错误] 没有解析到 DOI", file=sys.stderr)
        return 1
    results = []
    for index, doi in enumerate(dois, start=1):
        result = lookup(doi, email=args.email, fallback=not args.no_fallback)
        results.append(result)
        if not args.json and not args.output:
            status = "OA" if result.get("is_oa") else "非 OA/未知"
            print(f"[{index}/{len(dois)}] {doi} -> {status} {result.get('best_pdf') or ''}")
        time.sleep(max(0.0, args.sleep))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {args.output}")
    elif args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
