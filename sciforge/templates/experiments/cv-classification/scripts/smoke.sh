#!/usr/bin/env bash
# CPU 离线自检：不下载数据、不依赖 torch，1 分钟内完成。
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${SCIFORGE_RUN_DIR:-outputs/smoke}"
python -m src.train --smoke --output-dir "$OUT" "$@"
