# タスク: regression-swap — 既存ケースの失効と新ケースの組み込み

## ゴール
影響 ID に紐づく既存リグレッションケースを `仕様の状態=失効(<新ID>)` に更新し、新ケースを追記し、追跡表を更新する。2 つの検査（trace-check / test-weaken-check）が NG なら全部を元に戻す。

## 触るファイル（これ以外は変更しない）
- `qa_sentinel/core/swap.py` — 新規。`swap(project, impact_ids, new_cases, cwd) -> SwapResult`
- `tests/test_swap.py` — 新規。`tests/fixtures/swap/` に最小の `system_test_cases.csv` と `traceability-matrix.md` を置く

## 実装の指示
- CSV の列は yuki-aidd-kit `02_共通/ひな形/test/system_test_cases.csv` のヘッダ（`テストID,ロール,対象機能,ツアー観点,テスト目的,前提条件,手順,期待される結果,severity,結果,実施日,実施者,DEF,根拠の版,仕様の状態`）。列が足りない旧 CSV は末尾に足す
- 失効の判定: 行の `根拠の版` が `impact_ids` のいずれかで始まる（`REQ-F-003@...`）、または `対象機能` が新ケースと同じ。**削除しない**。`仕様の状態` を `失効(<新ID>)` にする（連番は詰めない。ID 体系は kit `traceability.md`）
- 新ケースの ID は CSV 内最大番号 +1 から採番（`ST-013`…）。`根拠の版` は `<REQ-ID>@<版>`。版は kit の `scripts/section_hash.py` があれば呼ぶ、無ければ空欄
- 追跡表 `traceability-matrix.md`: 影響 REQ 行の ST/UT 列に新 ID を追記（カンマ区切り）。失効 ID は残す
- 書き換え前に対象 2 ファイルを `.swap.bak` にコピー。`core.gates.run_gate("trace-check")` と `run_gate("test-weaken-check")` のどちらかが `ok=False` なら bak から戻し、`SwapResult(ok=False, reason=summary)`
- `SwapResult`: `ok`, `retired: list[str]`, `added: list[str]`, `reason: str | None`
- 決定論的。LLM を呼ばない

## 完了条件
- [ ] `bash scripts/verify.sh` が `ALL GREEN`
- [ ] `tests/test_swap.py`: 失効と追記 / 追跡表の更新 / gate NG で戻る / 旧 CSV（列不足）の 4 ケース
- [ ] 変更が上記「触るファイル」内に収まっている

## スコープ外（やらないこと）
- ケース内容の生成（LLM の仕事。ここは表の操作だけ）
- git 操作
