# システムテスト仕様・結果 — 社内図書館 貸出管理

> 検証対象: `01-requirements.md` の `REQ-F` / `REQ-N` を本番相当構成で end-to-end。
> ケースの詳細（手順・期待値・観点）は `docs/test/system_test_cases.csv` が真実源。ここは ID と対象 REQ の対応だけを持つ。

**日付**: 2026-09-29 ／ **実行環境**: Chrome・Windows 11・社内 LAN ／ **前提**: 結合テストが全て pass

## テストケース

| ID | 対象 REQ | 観点 | 測定条件 | 判定基準 | 実測値 | 合否 | evidence | 実施日 | 実施者 |
|---|---|---|---|---|---|---|---|---|---|
| ST-001 | REQ-F-001 | 認証 | 未認証アクセスの遮断 | ログイン画面へ移る | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-002 | REQ-F-001 | 認証 | ログイン正常系 | ダッシュボードへ遷移する | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-003 | REQ-F-002 | 蔵書検索 | 検索から結果表示まで | 結果が表示される | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-004 | REQ-F-002 | 蔵書検索 | 0 件・異常入力 | 「該当なし」と次の操作が出る | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-005 | REQ-F-003 | 貸出 | 貸出上限 5 冊の境界 | 4 冊は可、6 冊目は拒否 | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-006 | REQ-F-003 | 貸出 | 上限超過のエラー表示 | 上限 5 冊のエラーが出て記録されない | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-007 | REQ-F-004 | 返却 | 貸出から返却までのライフサイクル | 冊数と蔵書状態が正しく変わる | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-008 | REQ-F-005 | 通知 | 期限前日の通知 | 1 通だけ届く | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-009 | REQ-N-002 | レイアウト | 1366×768 で主要導線が切れない | 水平スクロールなし | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-010 | REQ-N-001 | 応答 | 検索・貸出の応答時間 | 95 パーセンタイル 2 秒以内 | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-011 | REQ-N-002 | キーボード操作 | キーボードだけで貸出まで完了 | Tab/Enter/Space だけで完了 | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |
| ST-012 | REQ-N-002 | 確認ダイアログ | Esc とフォーカスの閉じ込め | Esc で閉じ、フォーカスが戻る | 未実施 | 未実施 | `docs/test/system_test_cases.csv` | - | - |

- 実施結果は CSV の「結果」列に記録し、集計は `test-metrics.sh` で行う（手書きしない）

### 観点チェック

- [ ] 性能効率性（ST-010）
- [ ] 互換性・デバイス（ST-009）
- [ ] 使用性・アクセシビリティ（ST-011・ST-012）

## 検出した欠陥

| DEF-ID | 対象 ST | 対象 REQ | severity | 内容 | evidence | 起票日 | 対応 |
|---|---|---|---|---|---|---|---|
| - | - | - | - | なし | - | - | - |

## 出口確認

- [ ] 全 `REQ-N` に1件以上の `ST-xxx` があり、実測値が記録されている
- [ ] Critical / High の欠陥が残ゼロ

## 承認

- 承認記録: `docs/lifecycle/approvals/phase-7.md` — **判定・根拠・承認者は人間が埋める**（AI は埋めない）
- 事前レビュー: `/phase-review 7`（AI 3 役が指摘を出し切る。AI は承認しない）
- 有効性の確認: `./scripts/check-approval.sh --phase 7`（exit 0 なら次工程へ進んでよい）
- **承認後にこの文書を変更すると承認は自動失効する**（`reviewed_hash` の不一致で検出）。変更したら取り直す
