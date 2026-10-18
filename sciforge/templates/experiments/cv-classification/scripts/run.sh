#!/usr/bin/env bash
# 训练启动脚本：可被 sciforge exp launch 直接调用。
set -euo pipefail
cd "$(dirname "$0")/.."

# SciForge 远端启动时注入 runs/<run_id>，保证 metrics 落在 exp_results 期望的位置
OUT="${SCIFORGE_RUN_DIR:-outputs/$(date +%Y%m%d-%H%M%S)}"
python -m src.train --config configs/default.yaml --output-dir "$OUT" "$@"
