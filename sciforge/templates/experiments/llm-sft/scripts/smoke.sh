#!/usr/bin/env bash
# CPU 离线自检：纯标准库，不下载模型/数据。
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${SCIFORGE_RUN_DIR:-outputs/smoke}"
python -m src.train --smoke --output-dir "$OUT" "$@"
