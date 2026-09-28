# タスク: 承認の機械判定（check-approval 連携）と、review/submit の差分表示

## ゴール
`approve` が `check-approval.sh`（kit）の結果を見てから `done` にする。Web で `blocked` タスクに回答でき、回答が `answer` と同じ経路で再開する。

## 触るファイル（これ以外は変更しない）
- `qa_sentinel/core/orchestrator.py` — `approve()` に `gate: bool = True` を足し、`core.gates.run_gate("check-approval")` が `ok=False` なら `ValueError(summary)`
- `qa_sentinel/web/app.py` — `GET /api/tasks/<id>/diff`: `task.branch` と本線の `git diff --stat` と本文（上限 200KB）を返す。git が無い／ブランチが無いときは `{"diff": null}`
- `qa_sentinel/web/static/index.html` — `review` のフォームに差分（変更ファイル一覧と本文）を表示。根拠（evidence）が空の段は OK ボタンを無効化し「根拠なし」と表示
- `tests/test_core.py` — 既存を壊さず、`approve(gate=False)` で既存テストを維持。`gate=True` で NG の 1 ケースと `/api/tasks/<id>/diff` の 1 ケースを追加

## 実装の指示
- `approve()` は `paused` 以外を拒否する既存の振る舞いを維持
- review/submit/answer/approve の API と UI は MVP に既にある。ここでは差分表示と根拠ガードだけ足す
- 人の名前（`by`）が空の送信は 400（既存の振る舞い）。AI が埋めない旨の文言は残す

## 完了条件
- [ ] `python scripts/verify.py` が `ALL GREEN`
- [ ] `tests/test_core.py` に approve の gate NG ケースと `/api/answer` の 1 ケースが追加され pass
- [ ] 変更が上記「触るファイル」内に収まっている

## スコープ外（やらないこと）
- 認証（ローカル限定の前提。`127.0.0.1` 以外で公開しない）
- git 操作
