#!/usr/bin/env bash
# CPU 离线自检：纯标准库，无需 transformers / 数据集。
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${SCIFORGE_RUN_DIR:-outputs/smoke}"
python -m src.train --smoke --output-dir "$OUT" "$@"
