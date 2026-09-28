# Codex への指示書 — T03: regression-swap（既存ケース失効 + 新ケース組み込み）

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
qa_sentinel/core/gates.py        run_gate(name, args, project, cwd=".", timeout=600) -> GateResult（T01 で完成済み。今回は使うだけ）
qa_sentinel/core/project.py      projects/<name>/config.toml を読む。Project.config["project"] に cases_csv・lifecycle_dir・branch がある
projects/library-loan/config.toml [project] に lifecycle_dir・cases_csv・branch がある
scripts/verify.py                検証ゲート。py_compile → pytest → CLI スモーク → 文書存在
tests/test_core.py, tests/test_gates.py 既存テスト（壊さない）
```

背景: qa-sentinel は「AI が下書き、人が直して OK」で QA 工程を回す。工程表の `regression-swap` 段は、仕様変更の影響 ID に紐づく既存リグレッションケースを失効させ、新ケースを追記し、追跡表を更新する。終了条件は `trace-check.sh` と `test-weaken-check.py` の exit code（機械判定）。AI は **作業ブランチ**（`qa-sentinel/<task>`）にだけ書き、人の確定で本線へ進む。ケースは **削除しない**。古いケースは `仕様の状態=失効(<新ID>)` に更新するだけで残す。

## 2. ゴール

`qa_sentinel/core/swap.py` を新規作成し、影響 ID に紐づく既存ケースを失効させ、新ケースを CSV に追記し、追跡表（`traceability-matrix.md`）を更新する関数 `swap` を作る。書き換え前に対象ファイルを `.swap.bak` にバックアップし、`run_gate("trace-check")` と `run_gate("test-weaken-check")` のどちらかが NG なら bak から復元して失敗を返す。決定論的（LLM を呼ばない）。

## 3. 触るファイル（これ以外は変更しない）

- `qa_sentinel/core/swap.py` — 新規
- `tests/test_swap.py` — 新規
- `tests/fixtures/swap/` — 新規（最小の `system_test_cases.csv` と `traceability-matrix.md` を置く）

## 4. 実装の指示（推測の余地を残さない）

```python
# qa_sentinel/core/swap.py
from __future__ import annotations
import csv, shutil, subprocess
from dataclasses import dataclass
from pathlib import Path
from .project import Project
from .gates import run_gate

@dataclass
class SwapResult:
    ok: bool
    retired: list[str]
    added: list[str]
    reason: str | None = None

CSV_HEADER = [
    "テストID", "ロール", "対象機能", "ツアー観点", "テスト目的", "前提条件", "手順",
    "期待される結果", "severity", "結果", "実施日", "実施者", "DEF", "根拠の版", "仕様の状態",
]

def swap(project: Project, impact_ids: list[str], new_cases: list[dict], cwd: str | Path = ".") -> SwapResult: ...
```

- CSV パスは `cwd / project.config["project"]["cases_csv"]`。追跡表パスは `cwd / project.config["project"]["lifecycle_dir"] / "traceability-matrix.md"`（`lifecycle_dir` 配下、ファイル名固定）。
- **CSV 読み込み**: `csv.DictReader`。ヘッダに `CSV_HEADER` の列が足りない旧 CSV は、読み込んだ各行に不足列を空文字で補い、書き出し時のヘッダは `CSV_HEADER` に揃える（列を並べ替えない。既存の並びの後ろに不足列を追記する形でよいが、書き出しは必ず `CSV_HEADER` の順序・全 15 列にする）。
- **失効判定**（既存行のうち、以下のどちらかに該当する行）:
  - 列 `根拠の版` が `impact_ids` のいずれかで始まる（例: `REQ-F-003@abcd1234` は `REQ-F-003` で始まる）
  - 列 `対象機能` が、今回追加する `new_cases` のいずれかの `対象機能` と一致する
  - 該当行は **削除しない**。`仕様の状態` を `失効(<新ID>)` に上書きする。`<新ID>` はその行の失効理由となった新ケースの ID（対応する新ケースが複数ある場合は最初に一致したものの ID でよい）。連番は詰めない・既存 ID は変更しない。
- **新ケースの ID 採番**: `new_cases` の各要素は `対象機能` 等のケース内容を持つ dict（本文生成は呼び出し側の仕事。ここでは表操作のみ）。ID は CSV 内の既存 `テストID` のプレフィックス（例 `ST-`）ごとに最大番号 +1 から採番する（`ST-013`, `ST-014`, …）。プレフィックスは `new_cases` の各要素が明示する（例: `new_cases[i]["prefix"] = "ST"`）か、指定が無ければ CSV 内で最も出現数の多いプレフィックスを使う。
- **根拠の版**: `<REQ-ID>@<hash>` の形式。`REQ-ID` は対応する impact_id。`hash` は `scripts/section_hash.py` が存在すれば `subprocess` で呼び出して得た値、存在しなければ空欄（`<REQ-ID>@`）。
- **追跡表の更新**: `traceability-matrix.md` を読み、影響 REQ ID（`impact_ids` の各値）に対応する行の ST/UT 列（列名やテーブル構造は既存ファイルの形式に従う。Markdown テーブルの該当セルをテキスト処理で書き換える）に、新しく追加した ID をカンマ区切りで追記する。既存の（失効した）ID は消さずに残す。
- **バックアップと書き込み順序**:
  1. `system_test_cases.csv` と `traceability-matrix.md` を、それぞれ同じディレクトリに `<元のファイル名>.swap.bak` としてコピー（`shutil.copy2`）。
  2. 両ファイルを新しい内容で書き換える。
  3. `run_gate("trace-check", [], project, cwd)` と `run_gate("test-weaken-check", [], project, cwd)` を呼ぶ。
  4. どちらかが `ok=False`（ただし `skipped=True`＝未検査は数えない。git が無い環境では test-weaken-check が常に未検査になる）なら、2 つの `.swap.bak` から元の内容を復元し（`shutil.copy2` で戻す。bak ファイル自体は残してよい）、`SwapResult(ok=False, retired=[], added=[], reason=<NG だった gate の summary>)` を返す。両方 NG なら先に検査した方の summary を使う。
  5. 両方 `ok=True` なら `SwapResult(ok=True, retired=<失効させた既存ID一覧>, added=<新規ID一覧>, reason=None)` を返す。
- 決定論的。LLM を呼ばない。ケース内容（`根拠の版` 以外のテスト本文）は `new_cases` にすでに入っている値をそのまま書くだけで、生成はしない。
- 型ヒントと docstring は既存ファイルと同じ密度（1〜2 行）。print しない。ログも不要。

## 5. テスト（tests/test_swap.py）— この 4 ケースを書く

`tests/fixtures/swap/` に最小の `system_test_cases.csv`（15 列ヘッダ、既存 ST 行を数行）と `traceability-matrix.md`（REQ 行に ST/UT 列を持つ最小 Markdown テーブル）を置く。テストでは `tmp_path` にこれらをコピーしてから使う。`Project(name="p", root=tmp_path, config={"project": {"lifecycle_dir": "docs/lifecycle", "cases_csv": "docs/quality/system_test_cases.csv", "branch": "main"}}, env_md="")` を使う。`qa_sentinel.core.swap.run_gate` を `monkeypatch` して両方 `ok=True`（または片方 `ok=False`）の `GateResult` を返すようにする。

1. **失効+追加**: `impact_ids=["REQ-F-003"]`、`new_cases` に 1 件。既存の該当行が `仕様の状態=失効(ST-0xx)` になり、新 ID が CSV に追記されることを確認。`SwapResult.ok is True`、`retired` と `added` が正しい。
2. **追跡表の更新**: 実行後、`traceability-matrix.md` の該当 REQ 行の ST/UT 列に新 ID がカンマ区切りで追記され、既存の失効 ID は残っていることを確認。
3. **gate NG → 復元**: `run_gate` を `ok=False` を返すよう monkeypatch し、実行後に CSV と traceability-matrix.md の内容が実行前と完全に一致すること（バックアップから復元済み）、`SwapResult(ok=False, reason=...)` であることを確認。
4. **旧 CSV（列不足）**: `根拠の版`・`仕様の状態` 列が無い旧形式の CSV で実行し、書き出し後のヘッダが `CSV_HEADER`（15 列）に揃っていること、既存データが失われていないことを確認。

## 6. 完了条件

- [ ] `python scripts/verify.py` の末尾が `ALL GREEN`
- [ ] `python -m pytest -q tests/test_swap.py` が 4 passed
- [ ] 変更が「触るファイル」内（`qa_sentinel/core/swap.py`, `tests/test_swap.py`, `tests/fixtures/swap/*`）に収まっている（変更したファイルの一覧を自分で書き出して確認する（git は無い））

## 7. スコープ外（やらないこと）

- ケース内容の生成（LLM の仕事。ここは表の操作だけ）
- orchestrator / runtime の変更
- git 操作

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
作ったファイル: <パス>, <パス>
verify.py の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
