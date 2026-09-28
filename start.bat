@echo off
rem qa-sentinel を始める（Windows）。ダブルクリックで: 初回は準備 → 画面を起動 → ブラウザを開く
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo 初回の準備をしています（1〜2 分）...
  python -m venv venv || (echo Python 3.11 以上を入れてください & pause & exit /b 1)
  venv\Scripts\pip install -q -e .[dev]
)
start "" http://127.0.0.1:8790/
venv\Scripts\python -m qa_sentinel.cli web --port 8790
