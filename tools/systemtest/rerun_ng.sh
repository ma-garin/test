#!/usr/bin/env bash
# NG になったユースケースを、実ブラウザで動画を撮りながら再実行する。
#
#   tools/systemtest/rerun_ng.sh [出力先]
#
# 専用の SQLite を作り直してから流すので、開発用の DB には触れない。
# 途中で失敗したらそこで止める（証跡が半端なまま「完了」にしない）。
set -euo pipefail

cd "$(dirname "$0")/../.."

PY=${PY:-.venv/bin/python}
PORT=${PORT:-8009}
OUT=${1:-docs/systemtest/evidence}
WORK=var/systemtest
DB="$WORK/rerun.sqlite3"

export DATABASE_URL="sqlite:///$(pwd)/$DB"
export DJANGO_SETTINGS_MODULE=config.settings.local

mkdir -p "$WORK"
rm -f "$DB"

echo "== 1/4 ブラウザ用のDBを作る =="
$PY manage.py migrate --no-input >/dev/null

echo "== 2/4 NGケースの再実行計画を組む =="
$PY manage.py build_rerun_plan --out "$WORK/rerun-plan.json"

if ! grep -q '"case_id"' "$WORK/rerun-plan.json"; then
  echo "NG ケースがありません。再実行は不要です。"
  exit 0
fi

echo "== 3/4 開発サーバーを起動する（127.0.0.1:$PORT） =="
$PY manage.py runserver "127.0.0.1:$PORT" --noreload >"$WORK/server.log" 2>&1 &
SERVER_PID=$!
# 起動を待たずに叩くと接続拒否で全件 NG になる。応答を確認してから進む。
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/healthz/" >/dev/null; then break; fi
  sleep 1
done

curl -sf "http://127.0.0.1:$PORT/healthz/" >/dev/null

echo "== 4/4 実ブラウザで再実行し、動画とスクリーンショットを撮る =="
$PY tools/systemtest/rerun_with_video.py \
  --plan "$WORK/rerun-plan.json" \
  --base-url "http://127.0.0.1:$PORT" \
  --out "$OUT"
