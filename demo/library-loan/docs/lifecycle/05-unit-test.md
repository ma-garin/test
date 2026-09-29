# 単体テスト仕様・結果 — 社内図書館 貸出管理

> 検証対象: `03-detailed-design.md` の `DD-xxx`（V字の対応）。
> **テストケースは実装コードでなく DD を入力にして作る。**

**日付**: 2026-09-29 ／ **実行環境**: Python 3.12 ／ **フレームワーク**: pytest

## テストケース

| ID | 対象 DD | 区分 | 観点 | 入力 | 期待結果 | テストコード | 結果 | 実施日 | 実施者 |
|---|---|---|---|---|---|---|---|---|---|
| UT-001 | DD-001 | 正常系 | 有効な ID | 有効な ID とパスワード | セッションが発行される | `tests/test_unit.py::test_ut001` | pass | 2026-09-29 | 図書係 |
| UT-002 | DD-001 | 異常系 | パスワード不一致 | 誤ったパスワード | 拒否され、セッションは発行されない | `tests/test_unit.py::test_ut002` | pass | 2026-09-29 | 図書係 |
| UT-003 | DD-001 | 境界値 | 空入力 | ID が空 | 拒否される | `tests/test_unit.py::test_ut003` | pass | 2026-09-29 | 図書係 |
| UT-004 | DD-002 | 正常系 | 部分一致 | 検索語「設計」 | 一致する蔵書が貸出状態つきで返る | `tests/test_unit.py::test_ut004` | pass | 2026-09-29 | 図書係 |
| UT-005 | DD-002 | 境界値 | 0 件 | 存在しない検索語 | 空配列が返る | `tests/test_unit.py::test_ut005` | pass | 2026-09-29 | 図書係 |
| UT-006 | DD-003 | 正常系 | 未返却の返却 | 未返却の貸出 | 返却日が記録され、蔵書が貸出可になる | `tests/test_unit.py::test_ut006` | pass | 2026-09-29 | 図書係 |
| UT-007 | DD-003 | 異常系 | 二重返却 | 返却済みの貸出 | 二重記録されず「返却済みです」 | `tests/test_unit.py::test_ut007` | pass | 2026-09-29 | 図書係 |
| UT-008 | DD-004 | 正常系 | 0 冊で貸出可 | 貸出中 0 冊 | ok=True | `tests/test_unit.py::test_ut008` | pass | 2026-09-29 | 図書係 |
| UT-009 | DD-004 | 境界値 | 4 冊で貸出可 | 貸出中 4 冊 | ok=True | `tests/test_unit.py::test_ut009` | pass | 2026-09-29 | 図書係 |
| UT-010 | DD-004 | 異常系 | 存在しない利用者 | 未登録の user_id | 例外が出る | `tests/test_unit.py::test_ut010` | pass | 2026-09-29 | 図書係 |
| UT-011 | DD-004 | 境界値 | canBorrow 上限 | 貸出中 5 冊 | ok=False、理由に上限 5 冊 | `tests/test_unit.py::test_ut011` | pass | 2026-09-29 | 図書係 |
| UT-012 | DD-005 | 正常系 | 期限前日の通知 | 期限が明日の未返却 1 件 | その利用者へ 1 通送られる | `tests/test_unit.py::test_ut012` | pass | 2026-09-29 | 図書係 |

- 区分は **正常系 / 異常系 / 境界値** の3つ
- 外部依存（メール送信・時刻）はモック化する

## 実行結果（evidence）

```text
pytest tests/ -v → 12 passed
```

- 実行コマンド: `pytest tests/ -v`

## 検出した欠陥

| DEF-ID | 対象 UT | 対象 DD | severity | 内容 | evidence | 起票日 | 対応 |
|---|---|---|---|---|---|---|---|
| - | - | - | - | なし | - | - | - |

## 出口確認

- [x] 全 `DD-xxx` に1件以上の `UT-xxx` がある
- [ ] DD-005 は正常系のみ（異常系は IT-003 で補う）
- [x] Critical / High の欠陥が残ゼロ
- [x] 実装に合わせて期待値を緩めていない

## 承認

- 承認記録: `docs/lifecycle/approvals/phase-5.md` — **判定・根拠・承認者は人間が埋める**（AI は埋めない）
- 事前レビュー: `/phase-review 5`（AI 3 役が指摘を出し切る。AI は承認しない）
- 有効性の確認: `./scripts/check-approval.sh --phase 5`（exit 0 なら次工程へ進んでよい）
- **承認後にこの文書を変更すると承認は自動失効する**（`reviewed_hash` の不一致で検出）。変更したら取り直す
