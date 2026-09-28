# Codex への指示書 — T05: 承認の機械判定（check-approval 連携）と review/submit の差分表示

このファイル 1 枚をそのまま Codex に渡す。他の文書を読ませる必要はない（必要なものはここに書いてある）。

---

## 0. あなた（Codex）の役割

- このリポジトリ（qa-sentinel）の **指示書の範囲内の実装** を行う。ファイル編集まで。
- **git 操作はしない**（add / commit / push / branch を実行しない）。検証と commit は人（Claude）が行う。
- 「触るファイル」以外を編集しない。編集したくなったら、理由を最後の報告に書いて止まる。
- 推測で埋めない。分からないことは最後の報告に「質問」として書く。
- 完了の基準は 1 つ: `python scripts/verify.py`の末尾が `ALL GREEN`。

## 1. リポジトリの地図（これだけ知っていればよい）

```
qa_sentinel/core/orchestrator.py    approve(task_id, by, ledger) が最終承認。今回 gate 判定を足す
qa_sentinel/core/gates.py           T01 で作成済み。run_gate(name, args, project, cwd=".", timeout=600) -> GateResult
qa_sentinel/core/project.py         load_project(name, projects_dir="projects") -> Project(config は dict)
qa_sentinel/web/app.py              make_handler(ledger, runtime=None, projects_dir="projects")（既にこの引数がある）。GET/POST の一覧はファイル冒頭コメント
qa_sentinel/web/static/index.html   review フォームがある。OK ボタンの id は "ok"。根拠は t.evidence[t.phase] を「根拠」の下に描画済み
tests/test_core.py                  既存テスト（壊さない）。特に test_m1_walks_all_phases_stops_at_swap_then_approve と test_web_api_review_submit_answer_approve は approve(...) を位置引数 3 つで呼んでいる
scripts/verify.py                   検証ゲート。py_compile → pytest → CLI スモーク → 文書存在
```

背景: qa-sentinel は「AI が下書き、人が直して OK」で QA 工程を回す（docs/05_協業モデル.md §2）。根拠の無い AI の下書きは確定画面に出してはいけない。確定・承認は必ず人の名前で decisions[] に残る。最終承認 `approve` は今まで機械判定なしで即 done にしていたが、ここに `check-approval` ゲートを通す。

## 2. ゴール

1. `approve()` が `run_gate("check-approval", ...)` の結果を見てから `done` にする。
2. Web に `GET /api/tasks/<id>/diff` を追加し、作業ブランチと本線の差分を返す。
3. review 画面に差分表示と、根拠が無い段の OK ボタン無効化を足す。

## 3. 触るファイル（これ以外は変更しない）

- `qa_sentinel/core/orchestrator.py`
- `qa_sentinel/web/app.py`
- `qa_sentinel/web/static/index.html`
- `qa_sentinel/cli.py` — `approve(...)` の呼び出し 1 箇所のみ（他は触らない）
- `tests/test_core.py`

## 4. 実装の指示（推測の余地を残さない）

### (a) `qa_sentinel/core/orchestrator.py` — `approve`

- シグネチャを `approve(task_id: str, by: str, ledger: Ledger, gate: bool = True, projects_dir: str = "projects") -> Task` に変更する（既存の呼び出し `approve(task.task, "yuki", led)` は位置引数のままで動くこと。キーワード省略時は `gate=True, projects_dir="projects"`）。
  - **注意**: `load_project` に決め打ちの `"projects"` を渡してはいけない。CLI・Web それぞれが使っている projects ディレクトリと一致させるため、`projects_dir` 引数をそのまま `load_project` に渡す
- `paused` 以外を拒否する既存の振る舞い、`by` が空なら `ValueError` は維持する。
- `gate is True` のとき:
  - `from . import gates` を使い、`project = load_project(task.project, projects_dir)`（`.project` モジュールの `load_project`）
  - `result = gates.run_gate("check-approval", [], project, cwd=".")`
  - `result.exit_code == 127`（スクリプトが無い＝kit 未配布）のときは **ブロックしない**。`decisions` に積む approve エントリに `"gate": "missing"` を足して続行する（MVP なので kit が無い環境でも承認できる）
  - それ以外で `result.ok is False` のとき: `raise ValueError(f"check-approval NG: {result.summary}")`（台帳は更新しない。呼び出し前の状態のまま）
  - `result.ok is True` のときは `decisions` の approve エントリに `"gate": "ok"` を足す
- `gate is False` のときは gate 判定を一切呼ばず、既存のまま即 done（`decisions` の approve エントリに `"gate"` キーは付けない）
- 既存テストで `approve(t.task, "yuki", led)` のように kit スクリプトが無い前提で呼んでいる箇所は、`run_gate` が `exit_code=127` を返す経路（上記「ブロックしない」）を通ることになる。これは意図通りなので触らない

### (a') `qa_sentinel/cli.py` — `approve` 呼び出し 1 箇所のみ

- `approve(args.task, args.by, ledger)` を `approve(args.task, args.by, ledger, projects_dir=args.projects)` に変更する（`args.projects` は既存の `--projects` 引数。他の行・他のコマンドは一切触らない）

### (b) `qa_sentinel/web/app.py` — `GET /api/tasks/<id>/diff` と `approve` の呼び出し

- `make_handler` は既に `projects_dir: str = "projects"` を持っている（追加不要）。`do_POST` 内 `/api/approve` の分岐だけを直す: `approve(task, by, ledger)` を `approve(task, by, ledger, projects_dir=projects_dir)` に変更する
- `serve()` は `make_handler(ledger)` のままでよい（`projects_dir` 省略時は既定の `"projects"`）

`do_GET` に `/api/tasks/<id>/diff` の分岐を足す（`/api/tasks/` の分岐より前で判定する。パスは `/api/tasks/<id>/diff` の形）。

- `task = ledger.load(id)` で読む（`FileNotFoundError` は 404 で `{"error": "not found"}`）
- `subprocess.run(["git", "diff", "--stat", f"main...{task.branch}"], cwd=".", capture_output=True, text=True, timeout=10)` で stat を、`subprocess.run(["git", "diff", f"main...{task.branch}"], cwd=".", capture_output=True, text=True, timeout=10)` で本文を取る
- git バイナリが無い（`FileNotFoundError`）／`returncode != 0`（ブランチが無い等）のいずれでも `self._json({"stat": None, "diff": None})` を返す（例外を外に出さない）
- 成功時は `{"stat": <stat の stdout>, "diff": <diff の stdout を先頭 200*1024 バイトで切り詰めたもの>}` を返す
- 認証・CORS は追加しない（127.0.0.1 限定の前提のまま）

### (c) `qa_sentinel/web/static/index.html` — review フォーム

- `show(id)` の中、`t.status==='review'` の form 生成後（または detail 描画時）に `/api/tasks/<id>/diff` を fetch し、結果を detail 内に追加表示する: `<div class="muted">差分</div><pre>${stat}</pre><details><summary>差分本文</summary><pre>${diff}</pre></details>`。`stat`/`diff` が `null` なら「差分なし（git 不可）」等を出す
- `t.status==='review'` のとき、`t.evidence[t.phase]` が無いか空配列なら OK ボタン（id="ok"）を disabled にし、フォーム内に「根拠なし（AI の下書きに根拠が付いていないため確定できません）」を表示する。差し戻し（id="rej"）は無効化しない
- 既存の根拠一覧表示（`.ev`）はそのまま残す

## 5. テスト（tests/test_core.py に追記。既存は消さない）

1. `approve` が gate NG で `ValueError` になる: `monkeypatch` で `qa_sentinel.core.gates.run_gate`（or orchestrator が import している名前）を `ok=False, exit_code=1` の `GateResult` を返すよう差し替え、`approve(t.task, "yuki", led)` （既定 `gate=True`）が `ValueError` を送出し、`ledger.load(t.task).status` が `paused` のまま（done になっていない）ことを確認
2. スクリプトが無い環境（差し替えなし、実リポジトリに `scripts/check_approval.py` は無い前提）で `approve(t.task, "yuki", led, projects_dir=str(ws / "projects"))` が成功し `t.status == "done"` かつ `t.decisions[-1]["gate"] == "missing"`（`ws` fixture を使うテストにする。`load_project` が正しい projects ディレクトリを見るように必ず `projects_dir` を渡す）
3. `/api/tasks/<id>/diff` が存在しないブランチ／git 無しで `{"stat": None, "diff": None}` を返す（`make_handler` を使い、`test_web_api_review_submit_answer_approve` と同様の立て方でよい）

## 6. 完了条件

- [ ] `python scripts/verify.py` の末尾が `ALL GREEN`
- [ ] 全テスト pass（既存の `test_m1_walks_all_phases_stops_at_swap_then_approve` と `test_web_api_review_submit_answer_approve` を含む）
- [ ] 変更が「触るファイル」5 本（orchestrator.py, web/app.py, web/static/index.html, cli.py, tests/test_core.py）に収まっている

## 7. スコープ外（やらないこと）

- 認証・CORS の追加（ローカル限定の前提。`127.0.0.1` 以外で公開しない）
- `/api/tasks/<id>/diff` 以外の新規エンドポイント
- git 操作（add / commit / push / branch）

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
変更したファイル: <パス>, <パス>
verify.py の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
