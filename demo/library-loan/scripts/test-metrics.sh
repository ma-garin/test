#!/bin/bash
# test-metrics.sh — テスト工程の進捗と品質を工程文書と CSV の表から出す（薄いラッパ。本体は test_metrics.py）
#
# 使い方: ./scripts/test-metrics.sh [--root DIR] [--level UT|IT|ST|UAT] [--gate] [--history] [--into docs/test/iso29119-test-completion-report.md]
#   省略時は status（常に exit 0）。--gate は TESTING_STRATEGY.md §7 を読み 0=進める / 1=進めない / 2=判定できない。
#   判定不能を合格に数えない。推定（完了予定）には根拠を併記する。詳細は test-metrics-report.md。
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test_metrics.py" "$@"
