# システムテスト仕様・結果 — <プロジェクト名>

> 検証対象: `01-requirements.md` の `REQ-N`（非機能）＋本番相当構成での end-to-end。
> **実測値を必ず記録する。**「速い」「問題なし」は不合格の書き方。
> 判定基準の既定値は `skills/nfr-standards`、観点の詳細は `skills/dev-lifecycle/references/test-levels.md`。

**日付**: YYYY-MM-DD ／ **実行環境**: <!-- ブラウザ・OS・端末・回線条件 --> ／ **前提**: 結合テストが全て pass

## テストケース

| ID | 対象 REQ-N | 観点 | 測定条件 | 判定基準 | 実測値 | 合否 | evidence | 実施日 | 実施者 |
|---|---|---|---|---|---|---|---|---|---|
| ST-001 | REQ-N-001 | 性能効率性 | 3G相当(1.6Mbps) | 3秒以内 | 2.1秒 | 合 | `evidence/lighthouse.html` | 2026-09-19 | <実施者> |
| ST-0xx | REQ-N-0xx | デバイス対応 | 360×820px |  |  |  | `evidence/360.png` |  |  |
| ST-0xx | REQ-N-0xx | 信頼性 | オフライン |  |  |  |  |  |  |
| ST-0xx | REQ-N-0xx | セキュリティ |  |  |  |  |  |  |  |

<!-- 結果の語彙: pass / fail / blocked / skip / 未実施（UAT は 合 / 否 も可）。語彙外は「判定できない行」として分母に数える。集計は手書きせず ./scripts/test-metrics.sh -->

### 観点チェック（該当するものだけ実施。非該当は追跡表で `—`）

- [ ] 性能効率性（初回ロード / 主要操作の応答 / データ増加時の劣化）
- [ ] 互換性・デバイス（対象ブラウザ / 対象端末幅 / タッチターゲット寸法）
- [ ] 信頼性（オフライン動作 / 通信断からの復帰 / export→import の往復 / 異常終了後のデータ保全）
- [ ] セキュリティ（秘密情報の露出 / XSS: `innerHTML` 経路 / 越境データ）
- [ ] 使用性・アクセシビリティ（コントラスト比 4.5:1 / キーボード操作 / `lang` 指定）
- [ ] 長時間稼働（繰り返し操作でのメモリ・ストレージ増加）

## 検出した欠陥

| DEF-ID | 対象 ST | 対象 REQ-N | severity | 内容 | evidence | 起票日 | 対応 |
|---|---|---|---|---|---|---|---|

severity は ISTQB（Critical / High / Medium / Low）。severity と priority を混同しない。

## 出口確認

- [ ] 全 `REQ-N` に1件以上の `ST-xxx` があり、実測値が記録されている（`./scripts/trace-check.sh` NG=0）
- [ ] Critical / High の欠陥が残ゼロ
- [ ] Medium / Low の残件は `09-operations.md` の既知の制約へ引き継ぎ済み

## 承認

- 承認記録: `docs/lifecycle/approvals/phase-7.md` — **判定・根拠・承認者は人間が埋める**（AI は埋めない）
- 事前レビュー: `/phase-review 7`（AI 3 役が指摘を出し切る。AI は承認しない）
- 有効性の確認: `./scripts/check-approval.sh --phase 7`（exit 0 なら次工程へ進んでよい）
- **承認後にこの文書を変更すると承認は自動失効する**（`reviewed_hash` の不一致で検出）。変更したら取り直す
