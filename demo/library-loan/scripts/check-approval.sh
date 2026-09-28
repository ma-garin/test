#!/bin/bash
# check-approval.sh — 工程承認記録を機械検査する（薄いラッパ。本体は check_approval.py）
#
# 使い方: ./scripts/check-approval.sh [--root DIR] [--phase N | --gate N] [--quiet] [-o REPORT]
#   （省略時は全工程。--phase N は第N工程の承認状態、--gate N は「第N工程に着手してよいか」）
# 終了コード: 0=合格 / 1=未承認・失効・工程順序違反 / 2=判定不能（判定不能を合格に数えない）
# 仕様: templates/lifecycle/approvals/README.md。判定基準は phase-gates.md が真実源。
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_approval.py" "$@"
