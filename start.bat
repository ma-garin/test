@echo off
chcp 65001 >nul
rem このファイルは UTF-8。日本語の echo を正しく出すため上でコードページを切り替える
rem qa-sentinel を始める（Windows）。ダブルクリックで: 初回は準備 → 画面を起動 → ブラウザを開く
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo 初回の準備をしています（1〜2 分）...
  py -3 -m venv venv 2>nul || python -m venv venv || (echo Python 3.11 以上を入れてください（python.org。インストール時に「Add to PATH」を ON） & pause & exit /b 1)
  venv\Scripts\pip install -q -e .[dev]
)
set URL=http://127.0.0.1:8790/
dir /b tasks\*.json >nul 2>&1 || set URL=http://127.0.0.1:8790/guide
rem 画面が立ち上がってからブラウザを開く（初回は使い方の画面）
start "" /min cmd /c "timeout /t 2 >nul & start "" %URL%"
venv\Scripts\python -m qa_sentinel.cli web --port 8790
