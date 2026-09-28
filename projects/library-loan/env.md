# テスト環境定義 — library-loan（yuki-aidd-kit 図書貸出サンプル）

## 1. 被試験体（SUT）

| 項目 | 値 |
|---|---|
| 本体 | 単一 HTML `demo/library-loan/library-loan.html`（`app.css` / `app.js`）。`build.py` で結合。kit のスクリプトは `demo/library-loan/scripts/` に同梱 |
| 起動手順 | ブラウザでファイルを開く。サーバ不要。Playwright は `file://` で開く |
| データ置き場 | `localStorage`（ブラウザ内）。テスト前に `localStorage.clear()` |
| ソース変更 | 可（修整は最小差分。`build.py` を再実行） |
| 仕様 | `spec.md`、進捗 `CURRENT_STATE.md` |
| 工程文書 | `docs/lifecycle/`（`init-lifecycle.sh` で配置）、ケース表 `docs/test/system_test_cases.csv` |

## 2. アカウント

| 役割 | 参照名 |
|---|---|
| なし（認証なし） | — |

## 3. API 一覧

| API | 入力 | 出力 |
|---|---|---|
| なし（画面操作のみ） | | |

## 4. モック・補助

| 対象 | 起動 | 用途 |
|---|---|---|
| なし | | |

## 5. 既知の制約

- 貸出上限は `app.js` の定数。仕様変更の題材（5 冊 → 3 冊）はここに効く
- ダーク/ライト両テーマ。表示確認は両方
