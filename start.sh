#!/usr/bin/env bash
# qa-sentinel を始める（macOS / Linux）。初回は準備 → 画面を起動 → ブラウザを開く
cd "$(dirname "$0")"
[ -x venv/bin/python ] || { echo "初回の準備をしています（1〜2 分）..."; python3 -m venv venv && venv/bin/pip install -q -e .[dev]; }
URL=http://127.0.0.1:8790/; ls tasks/*.json >/dev/null 2>&1 || URL=http://127.0.0.1:8790/guide   # 初回は使い方の画面
( sleep 2; command -v xdg-open >/dev/null && xdg-open "$URL" || open "$URL" ) >/dev/null 2>&1 &
exec venv/bin/python -m qa_sentinel.cli web --port 8790
