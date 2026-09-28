# タスク: run_gate — 終了条件スクリプトをホスト側で実行し、exit code と要約を返す

## ゴール
段の終了条件（trace-check / test-metrics / test-weaken-check / check-approval / pw-spec-lint）を、LLM に解釈させずホストが判定できるようにする。Managed Agents の custom tool `run_gate` と、モック以外のランタイムの両方から同じ関数を呼ぶ。

## 触るファイル（これ以外は変更しない）
- `qa_sentinel/core/gates.py` — 新規。`run_gate(name, args, cwd) -> GateResult`
- `tests/test_gates.py` — 新規テスト

## 実装の指示
- `GateResult` dataclass: `name: str`, `exit_code: int`, `ok: bool`, `summary: str`（stdout の末尾 20 行以内。全量は返さない）, `report_path: str | None`
- `GATE_COMMANDS: dict[str, list[str]]` を 1 か所に定義:
  - `impact` → `["./scripts/trace-check.sh", "<lifecycle_dir>", "--impact", "<ID>"]`（ID は args[0]）
  - `trace-check` → `["./scripts/trace-check.sh", "<lifecycle_dir>"]`
  - `test-metrics` → `["./scripts/test-metrics.sh", "--gate"]`
  - `test-weaken-check` → `["python3", "scripts/test-weaken-check.py", "--base", "<branch>"]`
  - `check-approval` → `["./scripts/check-approval.sh"]`
  - `pw-spec-lint` → `["python3", "scripts/pw-spec-lint.py", "tests"]`
  - `cases` → 内部実装。`<cases_csv>` を読み、`仕様の状態` が空でない行、または期待結果が空の行があれば `ok=False`、summary に該当 ID を列挙
- `<lifecycle_dir>` `<cases_csv>` `<branch>` は `Project.config` から埋める（`run_gate(name, args, project)` の形にしてよい）
- スクリプトが無い（FileNotFoundError）ときは `exit_code=127, ok=False, summary="missing: <path>"`。例外を外に出さない
- `subprocess.run(..., capture_output=True, text=True, timeout=600)`。timeout は `exit_code=124`
- 未知の name は `ValueError`

## 完了条件
- [ ] `python scripts/verify.py` が `ALL GREEN`
- [ ] `tests/test_gates.py`: 未知 name / missing script / `cases` の空期待結果検出 / 正常（`echo` を使う偽スクリプトを tmp に置く）の 4 ケースが pass
- [ ] 変更が上記「触るファイル」内に収まっている

## スコープ外（やらないこと）
- Managed Agents への接続（T02）
- CSV の書き換え（T03）
- git 操作
