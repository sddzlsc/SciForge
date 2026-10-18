#!/usr/bin/env bash
# 微调启动脚本：可被 sciforge exp launch 直接调用。
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${SCIFORGE_RUN_DIR:-outputs/$(date +%Y%m%d-%H%M%S)}"
python -m src.train --config configs/default.yaml --output-dir "$OUT" "$@"
