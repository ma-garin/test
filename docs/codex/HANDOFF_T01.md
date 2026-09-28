# Codex への指示書 — T01: run_gate（終了条件スクリプトの実行器）

このファイル 1 枚をそのまま Codex に渡す。他の文書を読ませる必要はない（必要なものはここに書いてある）。

---

## 0. あなた（Codex）の役割

- このリポジトリ（qa-sentinel）の **指示書の範囲内の実装** を行う。ファイル編集まで。
- **git 操作はしない**（add / commit / push / branch を実行しない）。検証と commit は人（Claude）が行う。
- 「触るファイル」以外を編集しない。編集したくなったら、理由を最後の報告に書いて止まる。
- 推測で埋めない。分からないことは最後の報告に「質問」として書く。
- 完了の基準は 1 つ: `bash scripts/verify.sh` の末尾が `ALL GREEN`。

## 1. リポジトリの地図（これだけ知っていればよい）

```
qa_sentinel/core/state.py        段（PHASES）と状態の定義。順序の唯一の定義
qa_sentinel/core/project.py      projects/<name>/config.toml と env.md を読む。Project.config が dict
qa_sentinel/core/orchestrator.py 司令塔。段を進め、台帳に書く（今回は触らない）
qa_sentinel/runtime/base.py      Runtime / PhaseResult（今回は触らない）
projects/library-loan/config.toml [project] に lifecycle_dir・cases_csv・branch がある
scripts/verify.sh                検証ゲート。py_compile → pytest → CLI スモーク → 文書存在
tests/test_core.py               既存テスト（壊さない）
```

背景: qa-sentinel は「AI が下書き、人が直して OK」で QA 工程を回す。各段の**終了条件はスクリプトの exit code で機械判定**し、LLM に解釈させない。今回作る `run_gate` がその判定器。

## 2. ゴール

`qa_sentinel/core/gates.py` を新規作成し、名前で指定した終了条件スクリプトを実行して `GateResult` を返す関数 `run_gate` を作る。例外を外に出さず、無いスクリプト・タイムアウト・未知の名前をそれぞれ決まった形で返す。

## 3. 触るファイル（これ以外は変更しない）

- `qa_sentinel/core/gates.py` — 新規
- `tests/test_gates.py` — 新規

## 4. 実装の指示（推測の余地を残さない）

```python
# qa_sentinel/core/gates.py
from __future__ import annotations
import csv, subprocess
from dataclasses import dataclass
from pathlib import Path
from .project import Project

@dataclass
class GateResult:
    name: str
    exit_code: int
    ok: bool
    summary: str            # stdout+stderr の末尾 20 行以内。全量は返さない
    report_path: str | None = None

#: 名前 → コマンド。<lifecycle_dir> <cases_csv> <branch> は Project.config["project"] から埋める。<ID> は args[0]
GATE_COMMANDS: dict[str, list[str]] = {
    "impact":            ["./scripts/trace-check.sh", "<lifecycle_dir>", "--impact", "<ID>"],
    "trace-check":       ["./scripts/trace-check.sh", "<lifecycle_dir>"],
    "test-metrics":      ["./scripts/test-metrics.sh", "--gate"],
    "test-weaken-check": ["python3", "scripts/test-weaken-check.py", "--base", "<branch>"],
    "check-approval":    ["./scripts/check-approval.sh"],
    "pw-spec-lint":      ["python3", "scripts/pw-spec-lint.py", "tests"],
}
# "cases" だけはスクリプトではなく内部実装（下記）

def run_gate(name: str, args: list[str], project: Project, cwd: str | Path = ".", timeout: int = 600) -> GateResult: ...
```

- `name == "cases"`: `cwd / project.config["project"]["cases_csv"]` を `csv.DictReader` で読む。次のどちらかの行があれば `ok=False`、summary に該当 `テストID` を列挙（例 `確認待ち: ST-013 / 期待結果なし: ST-014`）。無ければ `ok=True, exit_code=0, summary="cases ok: N 件"`
  - 列 `仕様の状態` が空でない（`失効` で始まる行は除く）
  - 列 `期待される結果` が空
  - CSV が無い → `exit_code=127, ok=False, summary="missing: <path>"`
- スクリプト系: `subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)`。`ok = (returncode == 0)`
  - `FileNotFoundError` → `exit_code=127, ok=False, summary="missing: <cmd[0]>"`
  - `subprocess.TimeoutExpired` → `exit_code=124, ok=False, summary="timeout: <timeout>s"`
  - `<ID>` が必要なのに `args` が空 → `ValueError("impact には ID が要る")`
- 未知の name → `ValueError(f"unknown gate: {name}")`
- `report_path`: stdout に `詳細: <パス>` または `report: <パス>` の行があればそのパス。無ければ `None`
- 型ヒントと docstring は既存ファイルと同じ密度（1〜2 行）。print しない。ログも不要

## 5. テスト（tests/test_gates.py）— この 5 ケースを書く

`tmp_path` に偽の `scripts/` と `docs/quality/system_test_cases.csv` を置き、`Project(name="p", root=tmp_path, config={"project": {"lifecycle_dir": "docs/lifecycle", "cases_csv": "docs/quality/system_test_cases.csv", "branch": "main"}}, env_md="")` を使う。

1. 未知の name → `ValueError`
2. スクリプトが無い → `exit_code == 127` かつ `ok is False` かつ summary が `missing:` で始まる
3. 正常: `scripts/trace-check.sh` を `#!/bin/sh\necho "NG=0"\necho "詳細: out/report.md"\nexit 0` で作り（`chmod 755`）、`run_gate("trace-check", [], project, cwd=tmp_path)` が `ok is True` かつ `report_path == "out/report.md"`
4. `cases`: 期待結果が空の行と `仕様の状態=確認待ち: …` の行を含む CSV で `ok is False`、summary に両方の ID がある。`失効(ST-013)` の行は数えない
5. `impact` に args 無し → `ValueError`

CSV のヘッダは正確にこれ（15 列）:
`テストID,ロール,対象機能,ツアー観点,テスト目的,前提条件,手順,期待される結果,severity,結果,実施日,実施者,DEF,根拠の版,仕様の状態`

## 6. 完了条件

- [ ] `bash scripts/verify.sh` の末尾が `ALL GREEN`（`PY=python3 bash scripts/verify.sh` でもよい）
- [ ] `python3 -m pytest -q tests/test_gates.py` が 5 passed
- [ ] 変更が「触るファイル」2 本に収まっている（`git status --short` で確認してよい。add はしない）

## 7. スコープ外（やらないこと）

- Managed Agents への接続（T02）、CSV の書き換え（T03）、orchestrator / runtime の変更
- 既存テストの書き換え（壊れる場合は報告）
- `scripts/` 配下の実スクリプト作成（trace-check.sh 等は yuki-aidd-kit から配布される前提。ここでは呼ぶだけ）
- git 操作

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
作ったファイル: <パス>, <パス>
verify.sh の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
