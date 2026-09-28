# Codex への指示書 — T07: ループ上限（L5〜L7 の stopped 判定）

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
qa_sentinel/core/state.py        PHASES・上限の定数（MAX_REJECTS_PER_PHASE 等）。定義済み。変更しない
qa_sentinel/core/orchestrator.py 司令塔。review/submit/answer/approve/run_event（今回ここに追加）
qa_sentinel/core/ledger.py       Task（台帳の 1 レコード）。session_count フィールドを 1 つ足す
tests/test_core.py               既存テスト（壊さない。末尾に追加のみ）
scripts/verify.sh                検証ゲート
```

背景: docs/06_ループ設計.md の L5（差し戻し・reject 3 回）・L6（確認待ち・answer 5 回）・L7（セッション数・10 回）は、超えたら `stopped` にして人へ戻すループ。この判定は司令塔が台帳（decisions・history）を数えて行う。

L7 は本来「history のセッション ID 列」を数える設計（docs/06 §4）だが、本タスクでは意図的に簡略化する: `Task.session_count` という整数フィールドを台帳に持ち、`runtime.start()` を呼ぶたびに +1 する。history からセッション ID の切り替わりを数え直す実装は複雑になるため採用しない。ただし history がセッション ID を失わないよう、`run_event` の `running` 記録に `session=handle.id` を extra として添える（`task.record(phase, "running", session=handle.id)`）。

## 2. ゴール

`orchestrator.py` に純粋関数 `check_limits(task) -> str | None` を追加し、L5/L6/L7 の上限超過を検出して `review`・`answer`・`run_event` の入口で `stopped` にする。

## 3. 触るファイル（これ以外は変更しない）

- `qa_sentinel/core/orchestrator.py`
- `qa_sentinel/core/ledger.py` — `Task` に `session_count` フィールドを足すだけ
- `tests/test_core.py` — 末尾にテストを追加するだけ（既存のテストは 1 行も変えない）

## 4. 実装の指示（推測の余地を残さない）

`ledger.py`:
- `Task` に `session_count: int = 0` を追加する（他のフィールドの並び・型ヒントの書き方に合わせる）。`to_dict`/`load` はそのまま dataclass の仕組みで往復する（変更不要）。

`orchestrator.py`:
- 上限定数は `from .state import ... , MAX_REJECTS_PER_PHASE, MAX_QUESTIONS_PER_TASK, MAX_SESSIONS_PER_TASK` で読み込む。**再定義しない**。
- 追加する関数（他の関数と同じ密度で docstring 1 行）:

```python
def check_limits(task: Task) -> str | None:
    """判定順は本タスクで決める: sessions > questions > rejects（docs/06 §3 には順序の記載なし）。"""
    if task.session_count >= MAX_SESSIONS_PER_TASK:
        return "sessions_exceeded"
    if sum(1 for d in task.decisions if d["kind"] == "answer") >= MAX_QUESTIONS_PER_TASK:
        return "questions_exceeded"
    rejects = sum(1 for d in task.decisions if d["kind"] == "reject" and d["phase"] == task.phase)
    if rejects >= MAX_REJECTS_PER_PHASE:
        return f"rejects_exceeded:{task.phase}"
    return None
```

- 上限に当たったときの共通処理（`review`・`answer`・`run_event` で同じ形):

```python
reason = check_limits(task)
if reason:
    task.record(task.phase, "stopped", reason=f"{reason} — 進め方を見直してください（docs/06 §3）")
    ledger.save(task)
    return task
```

- 呼び出し箇所:
  - `review()`: `task.decisions.append(...)`（reject/confirm を積んだ直後）の後、`_continue` を呼ぶ前に上の共通処理を入れる。`ok=True`（confirm）のときも通してよい（reject 以外では reject 数は増えないため実害はない）。
  - `answer()`: `task.decisions.append(...)` の直後、`_continue` を呼ぶ前に入れる。
  - `run_event()`: `runtime.start(task, event, project)` を呼ぶ**前**に入れる。ただし新規タスク作成直後・`plan.drafter(phases[0]) == "human"` で早期 return する分岐より後、`handle = runtime.start(...)` の直前に置く。上限に当たったら `runtime.start` を呼ばずに `stopped` にして return する。
  - `run_event()` で `runtime.start` を実際に呼んだ直後（`task.session = {...}` を設定する行の近く）に `task.session_count += 1` を追加する。
  - `run_event()` のループ内 `task.record(phase, "running")` を `task.record(phase, "running", session=handle.id)` に変える（history にセッション ID を残す）。

## 5. テスト（tests/test_core.py に追記。既存のテストは変更しない）

`from qa_sentinel.core.orchestrator import check_limits` を import に追加してよい。

1. **reject 3 回で stopped**: `M4`（`test-design` の出口で確定待ち）で `run_event` → `review(..., ok=False)` を 3 回繰り返す（差し戻しのたびに `review` を呼び直す）。3 回目で `(task.phase, task.status) == ("test-design", "stopped")` かつ `task.reason` が `rejects_exceeded:test-design` を含む。
2. **answer 5 回で stopped**: `MockRuntime(blocked_at="test-design")` で `run_event` → `blocked` になったら `answer(...)` を呼ぶ。5 回目の `answer` の後、`task.status == "stopped"` かつ `task.reason` が `questions_exceeded` を含む（毎回 `blocked_at` に戻す必要があれば `MockRuntime(blocked_at="test-design")` を都度渡す）。
3. **session_count が 10 で run_event 前に stopped**: `led = Ledger(tmp_path / "tasks")`、`rt = MockRuntime()`、`task = Task(task=led.new_id(), project="library-loan", event="e", plan=M1.to_dict(), session_count=10)` を作り `led.save(task)`。`ev = nl.from_text("library-loan", "x")` を作り、`run_event(ev, rt, led, _project(), task=task)` を呼ぶ。`task.status == "stopped"` かつ `rt._n == 0`（`MockRuntime` は `start()` の呼び出し回数を `_n` に持つ。呼ばれていなければ 0 のまま）。
4. **上限に触れない既存フロー**: 既存の `test_m1_walks_all_phases_stops_at_swap_then_approve` 相当の M1 フローが上限追加後も同じ結果（`review`→`approve` まで通る）になることを確認する短いテストを 1 本足す（既存テストそのものは変更しない）。

## 6. 完了条件

- [ ] `bash scripts/verify.sh` の末尾が `ALL GREEN`（`PY=python3 bash scripts/verify.sh` でもよい）
- [ ] 追加した 4 テストが pass
- [ ] 既存の 14 テストがすべて pass（壊れていないこと）

## 7. スコープ外（やらないこと）

- `state.py` の定数の変更・再定義
- CLI・Web の変更
- git 操作

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
変えたファイル: <パス>, <パス>, <パス>
verify.sh の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
