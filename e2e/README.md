# E2E テスト（フェーズ2）

フェーズ1で作成した `docs/system_test_cases_phase1.csv`（762件）を全件読み込み、
Playwright のテストとして登録するデータ駆動スイート。

## ケースの扱い（3層）

| 層 | 例 | JSONレポート上の扱い |
|---|---|---|
| 自動検証 | 未認証リダイレクト、CSRF拒否、不正パラメータで500なし、画面表示、レスポンシブ、404、ログイン一式 | pass / fail |
| 証跡取得＋目視 | UI/UX観点（用語説明・並び順など）。表示到達までを自動検証し、スクリーンショット・動画を添付。注釈「要目視確認」付き | pass（要目視） |
| skip | 既存データのID・特定データ状態・複数テナント・データ操作が必要なもの | skipped（理由付き） |

## 前提

- Node.js 18 以上
- アプリが起動していること（別ターミナルで）:

```bash
make setup   # 初回のみ
make migrate
make seed    # デモ利用者 pmo@example.com（パスワードレス）
make run     # http://127.0.0.1:8000/

# ロール別利用者の投入（権限テストに必須・冪等）
.venv/bin/python manage.py shell < e2e/seed_role_users.py
```

なお `playwright.config.ts` の `webServer` により、サーバー未起動でもテスト実行時に
自動で起動・終了される（起動済みならそれを使う）。

## セットアップ

```bash
npm install
npx playwright install chromium
```

`node_modules/` が未追跡の場合はルートの `.gitignore` へ追加すること。

## 認証とロール別利用者

本システムの認証は**パスワードレス**（メールアドレスのみ。`apps/accounts/backends.py`）。
未知のメールアドレスでログインすると **PMO担当ロールの利用者が自動作成される**ため、
他ロールのテストを正しく走らせるには先に `e2e/seed_role_users.py` で利用者を投入すること。
投入せずに実行するとログイン自体は成功するが、全ロールがPMO担当として走り
権限テストの結果が信頼できなくなる。

メールアドレスは環境変数で上書きできる（パスワード変数は現行実装では未使用）。

| ロール | 環境変数 | 既定メールアドレス |
|---|---|---|
| PMO担当 | `E2E_USER_PMO` | pmo@example.com（make seed が作成） |
| PM・PL | `E2E_USER_PMPL` | pm@example.com |
| 品質責任者 | `E2E_USER_QUALITY` | quality@example.com |
| 変更管理者 | `E2E_USER_CHANGE` | change@example.com |
| 参照のみ | `E2E_USER_VIEWER` | viewer@example.com |
| テナント管理者 | `E2E_USER_TENANT_ADMIN` | tenantadmin@example.com |
| システム管理者 | `E2E_USER_SYSADMIN` | sysadmin@example.com |
| 無効化済み（TC-AUTH-053用） | `E2E_DEACTIVATED_EMAIL` | deactivated@example.com |

## 実行コマンド

```bash
# 全ロール並列実行（fullyParallel）
npx playwright test

# 接続先を変える場合
E2E_BASE_URL=http://127.0.0.1:8000 npx playwright test

# ロールを絞って実行（project = ロール）
npx playwright test --project=role-pmo
npx playwright test --project=anonymous

# 大分類を絞って実行（テストIDで grep）
npx playwright test --grep "TC-CTRL"
npx playwright test --grep "TC-QUAL"

# 1ケースだけ実行
npx playwright test --grep "TC-PMO-001 "
```

## 結果の確認

| 出力 | 場所 |
|---|---|
| 結果JSON（エラー詳細含む） | `e2e/results/results.json` |
| HTMLレポート | `e2e/results/html/`（`npm run e2e:report` で開く） |
| スクリーンショット・動画・トレース | `e2e/artifacts/` |

スクリーンショットは全テストで取得（`screenshot: 'on'`）、動画も全テストで記録
（`video: 'on'`）する設定。失敗時はトレース（`trace: 'retain-on-failure'`）も残る。

## 構成

```text
playwright.config.ts     ロール別 project・並列・レポーター設定
e2e/
├── auth.setup.ts        ロール別ログイン → storageState 保存
├── lib/
│   ├── roles.ts         ロール定義（accounts/constants.py と対応）
│   ├── csv.ts           フェーズ1 CSV の読み込み
│   ├── screens.ts       画面名 → URL の対応表（urls.py が出典）
│   └── checks.ts        自動検証の実装
└── tests/
    └── cases.spec.ts    CSV 762件をテストとして登録
```
