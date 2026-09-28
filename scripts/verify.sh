#!/usr/bin/env bash
# qa-sentinel 検証ゲート — 実体は scripts/verify.py（1 か所だけ保守する）。
#   PY=<python> bash scripts/verify.sh   または   python scripts/verify.py
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-$ROOT/venv/bin/python}"; [ -x "$PY" ] || PY=python3
exec "$PY" "$ROOT/scripts/verify.py" "$@"
