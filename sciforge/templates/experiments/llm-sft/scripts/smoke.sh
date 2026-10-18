#!/usr/bin/env bash
# CPU 离线自检：纯标准库，不下载模型/数据。
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${SCIFORGE_PYTHON:-${PYTHON:-python}}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then PYTHON=python3
  else echo "找不到 python，请先激活环境或设置 PYTHON=/path/to/python" >&2; exit 1; fi
fi
OUT="${SCIFORGE_RUN_DIR:-outputs/smoke}"
"$PYTHON" -m src.train --smoke --output-dir "$OUT" "$@"
