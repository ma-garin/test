#!/usr/bin/env bash
# qa-sentinel 検証ゲート — 「正しさ」の単一の真実。
# Codex はタスク完了前に必ずこれを green にする。Claude は commit 前に必ず実行する。
#   py_compile 全ソース → pytest 全件 → CLI スモーク（mock ランタイムで全段 + 承認）
# 終了コード 0 かつ末尾 "ALL GREEN" のときのみ合格。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/venv/bin/python}"; [ -x "$PY" ] || PY=python3
step(){ printf '\n==> %s\n' "$1"; }
fail(){ printf 'FAILED: %s\n' "$1"; exit 1; }

step "0. python >= 3.11（tomllib）"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' || fail "python 3.11 以上が必要"

step "1. py_compile"
"$PY" -m compileall -q qa_sentinel tests >/dev/null || fail "compile"

step "2. pytest"
"$PY" -m pytest -q tests || fail "pytest"

step "3. CLI スモーク（mock）"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
"$PY" -m qa_sentinel.cli --tasks "$TMP/tasks" run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更" | grep -q paused || fail "run → paused"
"$PY" -m qa_sentinel.cli --tasks "$TMP/tasks" approve T-0001 --by verify | grep -q done || fail "approve → done"
"$PY" -m qa_sentinel.cli --tasks "$TMP/tasks" run --project library-loan --nl "x" --mock-block-at test-design | grep -q blocked || fail "blocked 経路"
"$PY" -m qa_sentinel.cli --tasks "$TMP/tasks" answer T-0002 --text "拒否のみ" | grep -q paused || fail "answer → 再開"

step "4. 文書の存在"
for f in docs/00_概要.md docs/01_設計仕様.md docs/02_フロー.html docs/codex/README.md projects/_template/env.md; do [ -f "$f" ] || fail "missing $f"; done

echo; echo "ALL GREEN"
