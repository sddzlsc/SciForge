#!/usr/bin/env bash
# 训练启动脚本：可被 sciforge exp launch 直接调用。
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${SCIFORGE_PYTHON:-${PYTHON:-python}}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then PYTHON=python3
  else echo "找不到 python，请先激活环境或设置 PYTHON=/path/to/python" >&2; exit 1; fi
fi
OUT="${SCIFORGE_RUN_DIR:-outputs/$(date +%Y%m%d-%H%M%S)}"
"$PYTHON" -m src.train --config configs/default.yaml --output-dir "$OUT" "$@"
