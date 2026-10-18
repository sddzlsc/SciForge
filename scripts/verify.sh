#!/usr/bin/env bash
# SciForge 一键验证：核心测试 + 离线 demo + 模块自检
# 用法：bash scripts/verify.sh
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
export SCIFORGE_MOCK="${SCIFORGE_MOCK:-1}"
export SCIFORGE_HOME="${SCIFORGE_HOME:-$(mktemp -d)/sciforge-home}"
TMP="$(mktemp -d)"
FAIL=0

step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$1"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$1"; }
bad()  { printf '\033[1;31m✗ %s\033[0m\n' "$1"; FAIL=1; }

step "1/6 语法与导入"
"$PY" - <<'PY' && ok "导入" || bad "导入失败"
import sciforge, sciforge.cli, sciforge.demo
from sciforge.tools import list_tools, import_errors
print(f"  version={sciforge.__version__} tools={len(list_tools())}")
if import_errors:
    print("  pending modules:", ", ".join(import_errors))
PY

step "2/6 核心测试"
"$PY" -m pytest tests/ -q -x --ignore=tests/test_web.py 2>&1 | tail -3 && ok "pytest" || bad "pytest 失败"

step "3/6 离线 demo（含 pipeline/图/论文骨架）"
"$PY" -m sciforge.cli demo "$TMP/demo" >/dev/null 2>&1 \
  && [ -f "$TMP/demo/DEMO.md" ] && [ -d "$TMP/demo/.sciforge/runs" ] && ok "demo 产物齐全" || bad "demo 失败"

step "4/6 工具自检"
"$PY" -m sciforge.cli tools list >/dev/null 2>&1 && ok "工具注册表" || bad "工具注册表异常"
"$PY" -m sciforge.cli gpu status >/dev/null 2>&1 && ok "GPU 状态（本地降级）" || bad "gpu status 异常"
"$PY" -m sciforge.cli skills list >/dev/null 2>&1 && ok "Skills" || bad "skills 异常"

step "5/6 LLM 路由（Mock）"
"$PY" -m sciforge.cli llm test --mock --prompt "ping" >/dev/null 2>&1 && ok "LLM Mock 路由" || bad "LLM 路由异常"

step "6/6 Web 应用"
"$PY" - <<'PY' && ok "Web create_app / 健康检查" || bad "Web 异常"
from fastapi.testclient import TestClient
from sciforge.web.app import create_app
c = TestClient(create_app())
assert c.get("/").status_code == 200
assert c.get("/api/health").status_code == 200
assert c.get("/api/tools").status_code == 200
PY

printf '\n'
if [ "$FAIL" -eq 0 ]; then
  printf '\033[1;32m全部验证通过 ✓  演示项目：%s/demo/DEMO.md\033[0m\n' "$TMP"
else
  printf '\033[1;31m存在失败项，请检查上面的输出\033[0m\n'
fi
exit "$FAIL"
