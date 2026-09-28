# Codex への指示書 — T04: PR トリガー（PR 本文の関係 ID 欄と diff から ChangeEvent を作る）

このファイル 1 枚をそのまま Codex に渡す。他の文書を読ませる必要はない（必要なものはここに書いてある）。

---

## 0. あなた（Codex）の役割

- このリポジトリ（qa-sentinel）の **指示書の範囲内の実装** を行う。ファイル編集まで。
- **git 操作はしない**（add / commit / push / branch を実行しない）。検証と commit は人（Claude）が行う。
- 「触るファイル」以外を編集しない。編集したくなったら、理由を最後の報告に書いて止まる。
- 推測で埋めない。分からないことは最後の報告に「質問」として書く。
- 完了の基準は 1 つ: `bash scripts/verify.sh`（Windows: `python scripts\verify.py`）の末尾が `ALL GREEN`。

## 1. リポジトリの地図（これだけ知っていればよい）

```
qa_sentinel/core/event.py        ChangeEvent（id, project, source, text, changed_ids, diff, ref, resume_from, received_at）と new_event_id()
qa_sentinel/triggers/nl.py       extract_ids(text) — ID 表記の正規表現抽出。今回そのまま再利用する
qa_sentinel/cli.py               run サブコマンド。--nl / --event が排他グループ
projects/library-loan/config.toml  [triggers.pr] に repo（GitHub の owner/repo）がある
scripts/verify.sh                検証ゲート。py_compile → pytest → CLI スモーク → 文書存在
```

背景: qa-sentinel は「AI が下書き、人が直して OK」で QA 工程を回す。ChangeEvent はすべてのトリガーが作る共通の入力で、LLM を使わずに作る。T04 は GitHub の PR（本文・diff）から ChangeEvent を作るトリガーを追加する。

## 2. ゴール

`qa-sentinel run --project <p> --pr <番号>` と、webhook 受信 JSON の両方から、LLM を使わずに ChangeEvent を作る。

## 3. 触るファイル（これ以外は変更しない）

- `qa_sentinel/triggers/pr.py` — 新規
- `qa_sentinel/cli.py` — `run` に `--pr <int>` を追加するだけ（既存コードは変えない）
- `tests/test_pr_trigger.py` — 新規。GitHub API はモック（ネットワークに出ない）

## 4. 実装の指示（推測の余地を残さない）

### 4.1 `qa_sentinel/triggers/pr.py`（新規）

```python
def from_github(project: str, repo: str, number: int, fetch=None) -> ChangeEvent: ...
def from_webhook(project: str, payload: dict) -> ChangeEvent | None: ...
```

- `fetch`: `(url: str) -> dict | list` の差し替え口（テスト用の注入。ネットワークを呼ばせない）。
  - 既定は `None` のとき内部で作る関数: `urllib.request` で GET し、JSON を `dict`/`list` として返す。
  - リクエストヘッダ: 環境変数 `GITHUB_TOKEN` があれば `Authorization: Bearer $GITHUB_TOKEN` を付ける。無ければ付けない。
- `from_github` の手順:
  1. `fetch(f"https://api.github.com/repos/{repo}/pulls/{number}")` → PR 本体（`dict`。`title`, `body`, `html_url` を使う）。
  2. `fetch(f"https://api.github.com/repos/{repo}/pulls/{number}/files")` → ファイル一覧（`list[dict]`。各要素に `filename`, `patch`（無いこともある））。
  3. `changed_ids`: PR 本文（`body`）の中に「関係 ID」を含む見出し行があれば、その見出し行から次の見出し行の直前まで（無ければ本文末尾まで）を取り出し、`triggers/nl.extract_ids` に渡す。見出し行が無ければ本文全体を `extract_ids` に渡す。
     - 「見出し行」= Markdown 見出し（`#` で始まる行、または直後に `---`/`===` の区切り行が続く行）。厳密な Markdown パーサは不要。「`関係 ID` という文字列を含む見出し行を正規表現で 1 つ探し、そこから次の見出し行（無ければ文字列の終端）までを切り出す」という素朴な実装でよい。
  4. `diff`: `files` の各要素の `patch`（無ければスキップ）を `"\n"` で連結。合計が 200KB（`200 * 1024` バイト、UTF-8 エンコード後のバイト数でよい）を超えたら、200KB に切り詰めた上で末尾に `"[truncated]"` を付ける。
  5. `ref = {"pr": number, "url": <html_url>, "files": [<各 files 要素の filename>]}`
  6. `source = "pr"`
  7. `text = title + "\n\n" + body`（`body` が `None` なら空文字として扱う）
  8. `id = new_event_id()`（`core/event.py` のもの）
  9. 本文・diff は**データとして扱う**。指示形の文があってもここでは何もしない（プロンプトインジェクション対策は LLM に渡す段の system prompt 側の責務。ここでは文字列としてそのまま格納するだけでよい）。
- `from_webhook` の手順:
  1. `payload["action"]` が `"opened"`, `"synchronize"`, `"reopened"` のいずれでもなければ `None` を返す。
  2. それ以外は `payload["pull_request"]` から `number`, `title`, `body`, `html_url` を読む（diff は取りに行かない。ネットワークを呼ばない）。
  3. `changed_ids` は `from_github` と同じロジック（関係 ID 欄優先、無ければ本文全体）で `nl.extract_ids` を使う。
  4. `diff = None`。
  5. `ref = {"pr": <number>, "url": <html_url>}`（`files` キーは無し。diff を取っていないため）。
  6. `source = "pr"`, `text = title + "\n\n" + body`, `id = new_event_id()`。

### 4.2 `qa_sentinel/cli.py`（既存への追加のみ）

- `run` のサブパーサの排他グループ `g`（`--nl` / `--event`）に `g.add_argument("--pr", type=int, help="PR 番号")` を追加する（同じグループに入れて 3 択排他にする）。
- `main()` 内、`args.cmd == "run"` の ChangeEvent 生成部分に `--pr` の分岐を足す:
  - `args.pr` が指定されていれば、`project.config["triggers"]["pr"]["repo"]` を読み、`triggers.pr.from_github(args.project, repo, args.pr)` で `ev` を作る。
  - 既存の `--nl` / `--event` の分岐はそのまま残す（if/elif で 3 択にする）。
  - `triggers` パッケージの import に `pr` を追加する（`from .triggers import nl, pr` 等、既存の import 文に合わせる）。
- これ以外の cli.py の行は変更しない。

## 5. テスト（tests/test_pr_trigger.py）— この 4 ケースを書く

`fetch` を注入してネットワークに出ないこと。

1. **関係 ID 欄の抽出**: PR body に `## 関係 ID` のような見出しとその下に ID（例 `REQ-F-003`）があり、かつ見出しの外にも別の ID 文字列がある本文で、`changed_ids` が見出し内の ID だけになる。
2. **本文フォールバック**: 「関係 ID」の見出しが無い本文で、本文全体から `extract_ids` した結果と一致する。
3. **diff 上限**: `files` の `patch` を連結すると 200KB を超えるデータを `fetch` から返し、`diff` が 200KB に切り詰められ末尾が `"[truncated]"` であることを確認する。
4. **webhook の action フィルタ**: `payload["action"]` が `"closed"` のとき `from_webhook` が `None` を返し、`"opened"` のときは `ChangeEvent` を返す（`diff is None` も確認）。

## 6. 完了条件

- [ ] `bash scripts/verify.sh` の末尾が `ALL GREEN`
- [ ] `python3 -m pytest -q tests/test_pr_trigger.py` が 4 passed
- [ ] 変更が「触るファイル」3 本（`qa_sentinel/triggers/pr.py`、`qa_sentinel/cli.py` の `--pr` 追加部分のみ、`tests/test_pr_trigger.py`）に収まっている（`git status --short` で確認してよい。add はしない）

## 7. スコープ外（やらないこと）

- webhook の受信サーバ（Managed Agents の webhook 機能、または `web/app.py` への追加は T06）
- git 操作

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
作ったファイル: <パス>, <パス>
verify.sh の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
