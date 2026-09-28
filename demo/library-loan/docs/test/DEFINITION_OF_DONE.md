# Definition of Done（完了の定義）— <PROJECT>

**準拠**: IEEE 730-2014, ISTQB Foundation Level / **適用**: 全変更

## 原則

> **pytest が全テスト PASS であることは「完了」ではない。**それは L1/L2 の「コードが壊れていない」確認にすぎない。
> L3（ブラウザ確認）と L4（依頼者の確認）が完了して初めて「完了」である。

## ゲートの実行タイミング（本書内で最優先）

各所の「スキップ不可」「pre-commit が強制」は**マイルストーン時点の要求**として読む。日常のコミット単位では speed-harness H-7（ゲートはユーザー要求時のみ・`--no-verify` で直行）が優先する。

| タイミング | 実行するもの |
|---|---|
| 日常のコミット | 実行しない |
| **マイルストーン**（機能の区切り・PR マージ前・リリース判断） | フルゲート（`TESTING_STRATEGY.md` §4）。迷ったら実行する。結果を `docs/quality/evidence/` へ |
| ユーザー要求時 | 要求されたもの |

## 変更タイプ別 DoD

### Type A: バックエンド変更（`<src/**/*.py>`, `<web/**/*.py>`）

MANDATORY
- [ ] `<make test>` → 全 PASS + カバレッジ 80%+
- [ ] 構文チェック（`python -m py_compile` / `tsc --noEmit`）
- [ ] `quality_harness.py` PASS（新機能は `quality/feature_contracts.yml` に契約を追加済み）

REQUIRED（人間）
- [ ] code-reviewer で HIGH 以上の指摘なし
- [ ] 変更理由・影響範囲をコミットメッセージに記載

### Type B: フロントエンド変更 ★ 最重要（`<static/**/*.js>`, `<static/**/*.css>`, `<02_共通/ひな形/**/*.html>`）

MANDATORY
- [ ] `<make test>` → 全 PASS
- [ ] `<make verify-ui>` → E2E 全 PASS（`.ui-verified` 生成）
- [ ] `.ui-verified` の UI hash が現在の UI と一致（検証後に変更していない）

REQUIRED（人間・省略禁止）
- [ ] **ブラウザで実際に操作して確認（1920×1080）**、**1366×768 でレイアウト崩れなし**
- [ ] 変更した全ユーザーフローを最初から最後まで通す
- [ ] ブラウザコンソールにエラーなし
- [ ] `uiux_review` で全状態（通常・実行中・失敗・0件・狭い画面・モーダル）を確認
- [ ] スクリーンショットを `<tests/e2e/screenshots/>` に保存

PROHIBITED
- `pytest` PASS のみで完了と判断する ／ ブラウザ確認なしにコミット・プッシュする ／ E2E をスキップして UI 変更をコミットする ／ 「動くはず」で完了を宣言する

### Type C: ドキュメント変更（`docs/**/*.md`, `README.md`, `CLAUDE.md`, `AGENTS.md`）

- [ ] 内部リンク（相対パス）が有効
- [ ] 古い情報を更新した場合、関連文書も合わせて更新

## 機械的ゲート（pre-commit）

```text
git commit → UI ファイル staged？
  NO  → 秘密情報スキャン（scripts/pre-commit）
  YES → .ui-verified 確認（scripts/pre-commit-ui-gate.sh）
        新しい & hash 一致 → PASS ／ 不在・古い・不一致 → BLOCKED「make verify-ui を実行」
        .rebuild-mode あり → WARN（未検証と明示してコミット可）
```

## 違反時の対応

1. `iso29119-incident-report.md` 形式で記録 ／ 2. 影響評価 ／ 3. DoD を満たしてから再プッシュ ／ 4. 再発防止を `TESTING_STRATEGY.md` に反映（5 Whys / FMEA / CAPA のいずれかを名指しで使う）
