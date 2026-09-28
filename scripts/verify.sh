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
Q="$PY -m qa_sentinel.cli --tasks $TMP/tasks"
$Q run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更" --mode M1 --reviewer verify </dev/null | grep -q 確定待ち || fail "M1 run → 差し替えで確定待ち"
$Q ok T-0001 --by verify | grep -q 承認待ち || fail "ok → 承認待ち"
$Q approve T-0001 --by verify | grep -q done || fail "approve → done"
$Q run --project library-loan --nl "x" --mode M2 --reviewer verify </dev/null | grep -q 確定待ち || fail "M2 run → 設計で確定待ち"
$Q ok T-0002 --by verify | grep -q 手渡し || fail "M2 ok → 実行は手渡し"
$Q results T-0002 --by verify --file results.csv | grep -q 確定待ち || fail "results → 差し替えで確定待ち"
$Q run --project library-loan --nl "x" --mode M1 --reviewer verify --mock-block-at test-design </dev/null | grep -q 回答待ち || fail "blocked 経路"
$Q answer T-0003 --by verify "拒否のみ" | grep -q 確定待ち || fail "answer → 再開"
$Q run --project library-loan --nl "x" --mode M7 --reviewer verify </dev/null | grep -q 手渡し || fail "M7 → 人が下書き（セッション無し）"

step "4. 文書の存在"
for f in docs/00_概要.md docs/01_設計仕様.md docs/02_フロー.html docs/codex/README.md docs/05_協業モデル.md docs/codex/RUN_ALL.md scripts/verify.py projects/_template/env.md; do [ -f "$f" ] || fail "missing $f"; done

step "5. デモ同梱物（kit スクリプトが動く）"
( cd demo/library-loan && ./scripts/trace-check.sh docs/lifecycle >/dev/null 2>&1 ) || fail "demo trace-check"
[ -f demo/library-loan/docs/test/system_test_cases.csv ] || fail "demo cases csv"
rm -f demo/library-loan/trace-check-report.md

echo; echo "ALL GREEN"
