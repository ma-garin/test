# Codex 向け指示書

役割分担（webspec2doc `CODEX_GUIDE.md` と同じ）:

| 担当 | 仕事 |
|---|---|
| Claude | 設計・タスク分割・レビュー・最終確認・commit |
| Codex | 指示書の範囲内の実装（ファイル編集まで）。git は無い環境（Windows・zip）なので使わない。進捗は `PROGRESS.md`、成果は zip |

手順: `task-template.md` の型で書かれた T01〜T06 を 1 つずつ渡す。**渡すときは `HANDOFF_<番号>.md`（地図・指示・守ること・報告の形を 1 枚にしたもの）を使う。** 最初は `HANDOFF_T01.md`。完了条件は常に `python scripts/verify.py` が `ALL GREEN`。
指示書に無いファイルを触らない。仕様の根拠は `docs/01_設計仕様.md` と `docs/05_協業モデル.md`（AI は作業ブランチにだけ書く・製品コードは提案まで・根拠を必ず付ける）。SDK の呼び出し形は同 §6 と、必要なら公式ドキュメント（Managed Agents）を読む。

| # | タスク | 依存 |
|---|---|---|
| T01 | `run_gate` ホスト実行器（trace-check / test-metrics / test-weaken-check / check-approval を呼び exit code と要約を返す） | — |
| T02 | Managed Agents ランタイム（`runtime/managed_agents.py`） | T01 |
| T03 | regression-swap の実体（`core/swap.py`。CSV と追跡表の書き換え、戻し） | T01 |
| T04 | PR トリガー（本文の関係 ID 欄 ＋ diff → ChangeEvent） | — |
| T05 | 承認の機械判定（`check-approval.sh` 連携）と review 画面の差分表示・根拠ガード | T01 |
| T06 | トラッカー relay ポーラー（`qa-sentinel watch`。LLM を使わない） | T04 |
| T07 | ループ上限の判定（`docs/06_ループ設計.md` §4: reject 3 回・確認待ち 5 件・セッション 10 本で stopped） | T05 |
| T08 | Managed Agents 実機確認（予算 $1 で 1 段。`HANDOFF_T08_live_check.md`）。**今回は未実施（人の判断: mock まで）** | T01〜T07 |
| T09 | `trace_check.py`（bash 版 trace-check の Python 移植。git/bash 無しで gate を動かす） | T01 |

順序の推奨: T01 → T09 → T03 → T02 → T04 → T05 → T06 → T07。一括は `RUN_ALL.md`。

## T08 実機確認（Codex 環境でやり切る）

`HANDOFF_T08_live_check.md` を貼る。構築全体の順序は `BUILD_GUIDE.md`。

## （旧記載）T02 の実機確認について

Codex の T02 が終わったあと、Claude が Managed Agents に実際に接続して 1 段だけ回す。この環境には `ANTHROPIC_API_KEY` と `anthropic` パッケージが無いため未実行。必要なもの:
- `ANTHROPIC_API_KEY`（従量。予算 $1 でセッションを 1 本）
- `QA_SENTINEL_ENV_ID`（`client.beta.environments.create` で作った環境の ID）
- `GITHUB_TOKEN`（yuki-aidd-kit を読める）
- `pip install -e .[managed]`
手順: `python -m qa_sentinel.agent.register --project library-loan` → `qa-sentinel run --project library-loan --nl "貸出上限を 3 冊に" --mode M1 --reviewer <name> --budget 1 --runtime managed` → 台帳に `evidence` と `spent_usd` が入ることを確認。
