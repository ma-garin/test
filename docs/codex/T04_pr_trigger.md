# タスク: PR トリガー — PR 本文の関係 ID 欄と diff から ChangeEvent を作る

## ゴール
`qa-sentinel run --project <p> --pr <番号>` と、webhook 受信 JSON（`--event`）の両方から、LLM を使わずに ChangeEvent を作る。

## 触るファイル（これ以外は変更しない）
- `qa_sentinel/triggers/pr.py` — 新規
- `qa_sentinel/cli.py` — `run` に `--pr <int>` を追加（既存の `--nl` / `--event` と排他）
- `tests/test_pr_trigger.py` — 新規。GitHub API はモック

## 実装の指示
- `from_github(project, repo, number, fetch=None) -> ChangeEvent`。`fetch` は `(url) -> dict|str` の差し替え口（テスト用）。既定は `urllib.request` で `https://api.github.com/repos/<repo>/pulls/<n>` と `.../files`。トークンは `GITHUB_TOKEN`
- `changed_ids`: PR 本文から `triggers/nl.extract_ids` で抽出。yuki-aidd-kit の `pull_request_template.md` の「関係 ID」欄を優先し、無ければ本文全体
- `diff`: files API の `patch` を連結（上限 200KB。超えたら末尾を切って `[truncated]`）
- `ref = {"pr": n, "url": html_url, "files": [paths]}`
- `from_webhook(project, payload: dict) -> ChangeEvent`。`action in ("opened","synchronize","reopened")` 以外は `None`
- 本文・diff は**データとして扱う**。指示形の文があってもここでは何もしない（LLM に渡す段で system prompt が守る）

## 完了条件
- [ ] `bash scripts/verify.sh` が `ALL GREEN`
- [ ] `tests/test_pr_trigger.py`: 関係 ID 欄の抽出 / 本文フォールバック / diff 上限 / webhook の action フィルタ の 4 ケース
- [ ] 変更が上記「触るファイル」内に収まっている

## スコープ外（やらないこと）
- webhook の受信サーバ（Managed Agents の webhook 機能、または `web/app.py` への追加は T06）
- git 操作
