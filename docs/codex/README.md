# Codex 向け指示書

役割分担（webspec2doc `CODEX_GUIDE.md` と同じ）:

| 担当 | 仕事 |
|---|---|
| Claude | 設計・タスク分割・レビュー・最終確認・commit |
| Codex | 指示書の範囲内の実装（ファイル編集まで）。git 操作はしない |

手順: `task-template.md` の型で書かれた T01〜T06 を 1 つずつ渡す。**渡すときは `HANDOFF_<番号>.md`（地図・指示・守ること・報告の形を 1 枚にしたもの）を使う。** 最初は `HANDOFF_T01.md`。完了条件は常に `bash scripts/verify.sh` が `ALL GREEN`。
指示書に無いファイルを触らない。仕様の根拠は `docs/01_設計仕様.md` と `docs/05_協業モデル.md`（AI は作業ブランチにだけ書く・製品コードは提案まで・根拠を必ず付ける）。SDK の呼び出し形は同 §6 と、必要なら公式ドキュメント（Managed Agents）を読む。

| # | タスク | 依存 |
|---|---|---|
| T01 | `run_gate` ホスト実行器（trace-check / test-metrics / test-weaken-check / check-approval を呼び exit code と要約を返す） | — |
| T02 | Managed Agents ランタイム（`runtime/managed_agents.py`） | T01 |
| T03 | regression-swap の実体（`core/swap.py`。CSV と追跡表の書き換え、戻し） | T01 |
| T04 | PR トリガー（本文の関係 ID 欄 ＋ diff → ChangeEvent） | — |
| T05 | 承認の機械判定（`check-approval.sh` 連携）と review 画面の差分表示・根拠ガード | T01 |
| T06 | トラッカー relay ポーラー（`qa-sentinel watch`。LLM を使わない） | T04 |
| T07 | ループ上限の判定（`docs/06_ループ設計.md` §4: reject 3 回・確認待ち 5 件・セッション 10 本で stopped）。指示書は未作成 | T05 |

順序の推奨: T01 → T03 → T02 → T04 → T05 → T06。T02 の実機検証（API キー・予算）は Claude が行う。
