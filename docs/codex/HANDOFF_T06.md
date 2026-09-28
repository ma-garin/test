# Codex への指示書 — T06: トラッカー relay ポーラー（`qa-sentinel watch`）

このファイル 1 枚をそのまま Codex に渡す。他の文書を読ませる必要はない（必要なものはここに書いてある）。

---

## 0. あなた（Codex）の役割

- このリポジトリ（qa-sentinel）の **指示書の範囲内の実装** を行う。ファイル編集まで。
- **Codex 環境に git は無い。git 操作は行わない**。検証と commit は人（Claude）が行う。
- 「触るファイル」以外を編集しない。編集したくなったら、理由を最後の報告に書いて止まる。変更したファイルの一覧は自分で書き出して確認する（git status は使えない）。
- 推測で埋めない。分からないことは最後の報告に「質問」として書く。
- 完了の基準は 1 つ: `python scripts/verify.py`の末尾が `ALL GREEN`。

## 1. リポジトリの地図（これだけ知っていればよい）

```
qa_sentinel/core/event.py        ChangeEvent（source は SOURCES の一つ。"tracker" は既にある）
qa_sentinel/triggers/nl.py       extract_ids(text) — REQ-F-003 等の ID を正規表現で拾う
qa_sentinel/core/project.py      projects/<name>/config.toml を読む。Project.config が dict
qa_sentinel/core/plan.py         Plan.from_preset(mode, reviewer, budget_usd)
qa_sentinel/core/orchestrator.py run_event(event, runtime, ledger, project, plan=...)（今回は触らない）
qa_sentinel/core/ledger.py       Ledger.save の tmp→os.replace パターン（今回は触らない。tracker.py の永続化はこの書き方を真似る）
qa_sentinel/cli.py               サブコマンド定義。watch を追加する
projects/_template/config.toml   [triggers.tracker] mode/kind/base_url/query/poll_minutes/credential_ref
scripts/verify.py                検証ゲート
```

背景: 社内ネットにある JIRA / Confluence / Redmine を、ローカル常駐の決定論的ポーラーが見張り、更新を検知したときだけ ChangeEvent を作って `run` と同じ経路（`run_event`）に渡す。**イベントが無い間は LLM を一切起動しない**（`docs/00_概要.md` の制約: 監視は LLM を使わない決定論的コードが行う。定期的に自分を起こして確認する設計を持たない。cron や自己ウェイクの仕組みは作らない。常駐は systemd/launchd で人が行う）。

## 2. ゴール

`qa_sentinel/triggers/tracker.py` を新規作成し、`TrackerState`（永続化される状態）と `poll_once(project, state, fetch=None) -> list[ChangeEvent]` を作る。`qa_sentinel/cli.py` に `watch` サブコマンドを追加し、検知した ChangeEvent を 1 件ずつ `run_event` に渡す。

## 3. 触るファイル（これ以外は変更しない）

- `qa_sentinel/triggers/tracker.py` — 新規
- `qa_sentinel/cli.py` — `watch` サブコマンドの追加のみ（既存コマンドは変更しない）
- `tests/test_tracker.py` — 新規
- `projects/_template/config.toml` — `[triggers.tracker].credential_ref` を `"env:JIRA_TOKEN"` に変更（下記 4.2 参照）

## 4. 実装の指示（推測の余地を残さない）

### 4.1 `TrackerState`（dataclass）

```python
@dataclass
class TrackerState:
    last_seen: str    # ISO 8601 文字列
    polls: int = 0
    events: int = 0
    sessions_started: int = 0
```

- 永続化パス: `tasks/.tracker-<project>.json`（`tasks` は台帳ディレクトリ。呼び出し側から `Path` で渡す想定でよい）。
- 書き込みは `qa_sentinel/core/ledger.py` の `Ledger.save` と同じ **アトミック書き込み**（`*.json.tmp` に書いてから `os.replace`）。
- ファイルが無ければ `last_seen` は呼び出し時刻（UTC ISO、`timespec="seconds"`）で新規作成してよい。

### 4.2 `poll_once(project: Project, state: TrackerState, fetch=None) -> list[ChangeEvent]`

- `project.config["triggers"]["tracker"]` から `mode`, `kind`, `base_url`, `query`, `poll_minutes`, `credential_ref` を読む。

**設定エラー（try の外、関数の入口で判定。呼び出し元に例外をそのまま投げる。fetch は呼ばない）**:
- `mode == "direct"` はスコープ外。`mode` が `"relay"` 以外なら `NotImplementedError("direct モードの担当（未割当（別タスク））")`。
- `credential_ref` が `"vault:<name>"` → `NotImplementedError("direct モードの担当（未割当（別タスク））")`。
- `credential_ref` が `"env:<VAR>"` で `VAR` が `os.environ` に無い → `RuntimeError` を理由付きで送出（例: `RuntimeError(f"env var not set: {VAR}")`）。
- `credential_ref` がそれ以外の形式 → `ValueError(f"unknown credential_ref: {credential_ref}")`。
- 未知の `kind`（jira/confluence/redmine 以外） → `ValueError(f"unknown tracker kind: {kind}")`。
- 設定エラーは `state.polls` を増やす前、かつ try/except の外で判定・送出する（後述の「fetch/parse エラー」と混同しない）。

**種別ごとの URL 組み立て**（`kind` で分岐、設定エラーの判定を通過した後）:
  - `jira`: `GET <base_url>/rest/api/2/search?jql=<query> AND updated >= "<last_seen>"`
  - `confluence`: `GET <base_url>/rest/api/content/search?cql=lastmodified >= "<last_seen>"`
  - `redmine`: `GET <base_url>/issues.json?updated_on=>=<last_seen>`（`credential_ref` が `"env:<VAR>"` のとき、ヘッダは `Authorization: Bearer <os.environ[VAR]>`）。
- **fetch の注入**: 実 HTTP 呼び出しは行わず、`fetch(url: str, headers: dict) -> dict`（JSON 相当の dict を返す関数）を引数で受け取る。`fetch=None` の場合のデフォルト実装は `urllib.request` などで良いが、テストは必ず `fetch` を注入するので、デフォルト実装の実際の疎通は検証しない。
- **1 課題 = 1 ChangeEvent**:
  - `fetch` が返す dict から課題のリストを取り出す形式は各 `kind` のレスポンス構造に依存するので、jira なら `issues[].fields.summary`/`description`、confluence なら `results[].title`/本文、redmine なら `issues[].subject`/`description` のように、それぞれの実 API のレスポンス形（キー名）に沿って素直に実装してよい（テストは注入した `fetch` のダミー JSON に合わせて書く）。
  - `ChangeEvent(source="tracker", text=title + "\n\n" + body, changed_ids=nl.extract_ids(text), ref={"issue": key, "url": ...}, project=project.name, id=new_event_id())`（`id` は `qa_sentinel.core.event.new_event_id()` を使う）。
  - `key` と `url` も各 API のレスポンスから素直に取り出す（jira: `issues[].key` と `<base_url>/browse/<key>`、confluence: `results[].id` と `<base_url>` + `_links.webui`、redmine: `issues[].id` と `<base_url>/issues/<id>`、のように kind ごとに妥当な形でよい）。

**fetch/parse エラー（`fetch` 呼び出しと結果整形を try で囲む。ここだけログして飲み込む）**:
- `fetch` の呼び出しからレスポンスの整形（ChangeEvent 化）までを try/except で囲み、例外（通信エラー・JSON 不正・想定外のレスポンス形など）が起きたら内容を `sys.stderr` に出力し、`state.last_seen` は変更せず `poll_once` は空リスト `[]` を返す（呼び出し元に例外を伝播させない。落ちない）。
- 取得が成功した場合のみ、`state.last_seen` を今回のポーリング時刻（UTC ISO）に進める。
- `state.polls` は `poll_once` を呼ぶたびに `+1`（設定エラー以外は成功・失敗を問わない）。`state.events` は今回検知した ChangeEvent の件数を加算する。

### 4.3 `watch` サブコマンド（`qa_sentinel/cli.py`）

```
qa-sentinel watch --project <p> [--once] [--runtime mock|managed] [--mode <preset>] [--reviewer <名前>] [--budget <USD>]
```

- `--reviewer` は必須（Plan 生成に要る。4 問は聞かない。CLI 引数のみで組み立てる）。
- ループ本体: `load_project` → `TrackerState` をロード（無ければ新規）→ ループで `poll_once` を呼ぶ。
  - `--once` なら 1 回だけ実行してループを抜ける。
  - `--once` が無ければ、`poll_once` の後 `time.sleep(poll_minutes * 60)` してから次のポーリングへ（`poll_minutes` は `project.config["triggers"]["tracker"]["poll_minutes"]`）。
  - **cron 登録や自己ウェイクの仕組みは一切作らない**。常駐したいならこのプロセスを systemd/launchd 等で動かし続けるのは人の仕事（コード側では何もしない）。
- 各ポーリングで `poll_once` が返す ChangeEvent の件数が 0 件なら、標準出力に **`polled: 0 events` の 1 行だけ**を出し、台帳（`tasks/`）には何も書かない（`TrackerState` の永続化ファイルは更新してよいが、`Ledger`/Task 側には書かない、という意味）。
- 1 件以上あれば、各 ChangeEvent ごとに `Plan.from_preset(args.mode, args.reviewer, args.budget)` で Plan を作り、`core.orchestrator.run_event(ev, runtime, ledger, project, plan=plan)` を呼ぶ。呼ぶたびに `state.sessions_started += 1`。
  - `args.mode` が無ければ `ValueError` 等で落として構わない（`watch` では 4 問の対話はしない前提。`--mode` 必須として argparse の `required=True` にしてよい）。
  - `args.budget` は無ければ `project.config["session"]["budget_usd"]`（既定 5.0）を使う。
- `TrackerState` はポーリングのたびに（成功・失敗問わず）`tasks/.tracker-<project>.json` に保存する。

## 5. テスト（`tests/test_tracker.py`）— 4 ケース必須（`fetch` を注入する）

1. **0 件**: ダミー `fetch` が空のレスポンスを返す → `poll_once` の戻り値が `[]`、台帳ファイル（`tasks/T-*.json`）が作られない、`state.polls` が呼び出し前より `+1`、`state.sessions_started` は変化しない。
2. **1 件**: ダミー `fetch` が課題 1 件分の JSON を返す → 戻り値の `ChangeEvent` の `source == "tracker"`、`text` がタイトル+本文、`changed_ids` が `nl.extract_ids` の結果と一致、`ref` に `issue`/`url` がある。CLI 経路は `monkeypatch` で `core.orchestrator.run_event`（または `cli` がインポートした名前）を差し替えて呼ばれたことを確認する。
3. **fetch が例外を投げる**: `poll_once` 呼び出し後も `state.last_seen` が呼び出し前と変わらないこと（例外は外に漏れない）。
4. **`--once`**: `tests/test_core.py` と同様に `shutil.copytree(ROOT / "projects", tmp_path / "projects")` で `projects/` を `tmp_path` にコピーし、`monkeypatch` で `qa_sentinel.triggers.tracker.poll_once` を `[]` を返すよう差し替える（実ネットワークへは絶対に出ない）。`cli.main(["watch", "--project", "library-loan", "--once", "--mode", "M1", "--reviewer", "yuki", "--projects", str(tmp_path / "projects"), "--tasks", str(tmp_path / "tasks")])` を呼び、戻り値が `0`、標準出力に `polled: 0 events` が含まれること。

## 6. 完了条件

- [ ] `python scripts/verify.py` の末尾が `ALL GREEN`
- [ ] `python -m pytest -q tests/test_tracker.py` が 4 passed
- [ ] 変更したファイルの一覧を自分で書き出して確認し、「触るファイル」4 本（`qa_sentinel/triggers/tracker.py` 新規、`qa_sentinel/cli.py` の `watch` 追加のみ、`tests/test_tracker.py` 新規、`projects/_template/config.toml` の `credential_ref` 変更）に収まっている

## 7. スコープ外（やらないこと）

- `direct` モード（セッション内 custom tool ＋ vault、未割当（別タスク））
- cron / systemd / launchd 等の常駐設定ファイルの作成（常駐は人が行う。このプロセスは `--once` か素の `watch` ループを提供するだけ）
- git 操作（Codex 環境に git は無い）

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
作ったファイル: <パス>, <パス>
verify.py の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
