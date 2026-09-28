# タスク: 承認の機械判定と、確認待ちへの回答フォーム

## ゴール
`approve` が `check-approval.sh`（kit）の結果を見てから `done` にする。Web で `blocked` タスクに回答でき、回答が `answer` と同じ経路で再開する。

## 触るファイル（これ以外は変更しない）
- `qa_sentinel/core/orchestrator.py` — `approve()` に `gate: bool = True` を足し、`core.gates.run_gate("check-approval")` が `ok=False` なら `ValueError(summary)`
- `qa_sentinel/web/app.py` — `POST /api/answer`（`task`, `text`）。`orchestrator.answer` を呼ぶ。ランタイムは環境変数 `QA_SENTINEL_RUNTIME`（mock|managed、既定 mock）
- `qa_sentinel/web/static/index.html` — `blocked` のとき回答フォーム（textarea + ボタン）。送信後に再読
- `tests/test_core.py` — 既存を壊さず、`approve(gate=False)` で既存テストを維持。`gate=True` で NG の 1 ケースを追加（`run_gate` をモック）

## 実装の指示
- `approve()` は `paused` 以外を拒否する既存の振る舞いを維持
- `answer` はセッションを新しく作る（`continue`）。Web からの回答でも同じ
- フォームの `id` は固定（`answer-text`, `answer-send`）
- 承認ボタンの `by` は入力欄（`id="approver"`、既定 `web`）にする。AI が埋めない旨の文言は残す

## 完了条件
- [ ] `bash scripts/verify.sh` が `ALL GREEN`
- [ ] `tests/test_core.py` に approve の gate NG ケースと `/api/answer` の 1 ケースが追加され pass
- [ ] 変更が上記「触るファイル」内に収まっている

## スコープ外（やらないこと）
- 認証（ローカル限定の前提。`127.0.0.1` 以外で公開しない）
- git 操作
