# 単体テスト仕様・結果 — <プロジェクト名>

> 検証対象: `03-detailed-design.md` の `DD-xxx`（V字の対応）。
> **テストケースは実装コードでなく DD を入力にして作る**（実装の写しにすると欠陥を検出できない）。
> 実装・実行方法は `skills/test-automation`、技法の詳細は `skills/dev-lifecycle/references/test-levels.md`。

**日付**: YYYY-MM-DD ／ **実行環境**: ／ **フレームワーク**: pytest / vitest / その他

## テストケース

| ID | 対象 DD | 区分 | 観点 | 入力 | 期待結果 | テストコード | 結果 | 実施日 | 実施者 |
|---|---|---|---|---|---|---|---|---|---|
| UT-001 | DD-001 | 正常系 |  |  |  | `tests/test_x.py::test_a` | pass | 2026-09-19 | <実施者> |
| UT-002 | DD-001 | 異常系 | 入力不正 |  |  |  |  |  |  |
| UT-003 | DD-001 | 境界値 | 上限+1 |  |  |  |  |  |  |

<!-- 結果の語彙: pass / fail / blocked / skip / 未実施（UAT は 合 / 否 も可）。語彙外は「判定できない行」として分母に数える。集計は手書きせず ./scripts/test-metrics.sh -->

- 区分は **正常系 / 異常系 / 境界値** の3つ。各 `DD` に3区分すべてを1件以上作る
- 技法: 同値分割・境界値分析・分岐網羅・デシジョンテーブル
- 外部依存（LLM API・ネットワーク・時刻・乱数）はモック化する

## 実行結果（evidence）

```text
<!-- 例: pytest tests/ -v の出力サマリ。pass/fail 件数まで貼る -->
```

- 実行コマンド: 
- 集計は `./scripts/test-metrics.sh --level UT`（消化率・合格率・欠陥密度。手書きしない）

## 検出した欠陥

| DEF-ID | 対象 UT | 対象 DD | severity | 内容 | evidence | 起票日 | 対応 |
|---|---|---|---|---|---|---|---|
| DEF-0xx |  |  | Critical / High / Medium / Low |  |  | <起票日> | 修正済 / 未対応 / 受容 |

## 出口確認

- [ ] 全 `DD-xxx` に1件以上の `UT-xxx` がある（`./scripts/trace-check.sh` NG=0）
- [ ] 正常系・異常系・境界値の3区分がそろっている
- [ ] Critical / High の欠陥が残ゼロ
- [ ] 実装に合わせて期待値を緩めていない

## 承認

- 承認記録: `docs/lifecycle/approvals/phase-5.md` — **判定・根拠・承認者は人間が埋める**（AI は埋めない）
- 事前レビュー: `/phase-review 5`（AI 3 役が指摘を出し切る。AI は承認しない）
- 有効性の確認: `./scripts/check-approval.sh --phase 5`（exit 0 なら次工程へ進んでよい）
- **承認後にこの文書を変更すると承認は自動失効する**（`reviewed_hash` の不一致で検出）。変更したら取り直す
