# タスク: トラッカー relay ポーラー（`qa-sentinel watch`）— LLM を使わない監視

## ゴール
社内ネットにある JIRA / Confluence / Redmine を、ローカル常駐の決定論的ポーラーが見張り、更新を検知したときだけ ChangeEvent を作って `run` と同じ経路に渡す。イベントが無い間は LLM を一切起動しない。

## 触るファイル（これ以外は変更しない）
- `qa_sentinel/triggers/tracker.py` — 新規。`poll_once(project, state, fetch=None) -> list[ChangeEvent]` と `TrackerState`（`last_seen` の永続化 `tasks/.tracker-<project>.json`）
- `qa_sentinel/cli.py` — `watch --project <p> [--once] [--runtime mock|managed]`
- `tests/test_tracker.py` — 新規。REST はモック

## 実装の指示
- 種別ごとの取得（`config.toml [triggers.tracker]`）:
  - jira: `GET <base_url>/rest/api/2/search?jql=<query> AND updated >= "<last_seen>"`
  - confluence: `GET <base_url>/rest/api/content/search?cql=lastmodified >= "<last_seen>"`
  - redmine: `GET <base_url>/issues.json?updated_on=>=<last_seen>`
- 資格情報は `credential_ref`（`vault:<name>` または `env:<VAR>`）。`env:` のみ実装し、`vault:` は `direct` モードの担当（ここでは `NotImplementedError` に理由を書く）
- 1 課題 = 1 ChangeEvent（`source="tracker"`, `text`=タイトル＋本文, `changed_ids`=`nl.extract_ids`, `ref={"issue": key, "url": ...}`）
- `last_seen` は取得成功時だけ進める。失敗は log して次回に持ち越す（例外で落ちない）
- `watch` ループ: `time.sleep(poll_minutes*60)`。`--once` で 1 回だけ。イベント 0 件のときは台帳に何も書かず、標準出力に `polled: 0 events` の 1 行だけ
- LLM 起動回数を `tasks/.tracker-<project>.json` の `sessions_started` に記録（イベント 0 なら増えない）

## 完了条件
- [ ] `bash scripts/verify.sh` が `ALL GREEN`
- [ ] `tests/test_tracker.py`: 0 件で何も起きない / 1 件で ChangeEvent / 取得失敗で last_seen が進まない / `--once` の 4 ケース
- [ ] 変更が上記「触るファイル」内に収まっている

## スコープ外（やらないこと）
- `direct` モード（セッション内 custom tool ＋ vault）
- 自己ウェイク系の仕組み（cron 登録など）。常駐は systemd/launchd で人が行う
- git 操作
