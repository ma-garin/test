# フェーズ3 ODC分析レポート（サイクル1: 認証）

対象: フェーズ2.5 実行結果（Pass 50 / Fail 4 / Skip 716、`e2e/results/results-full-run1.json`）のFail 4件。

## 1. 根本原因（なぜ→なぜ）

- **観測**: setup（PMO担当ログイン）と TC-AUTH-052/053/057 の4件がタイムアウトまたは要素未検出で失敗。
- **なぜ①（直接原因)**: テストが `input[type="password"]` と `username` フィールドを操作しようとしたが、
  ログイン画面に存在しない（`templates/pages/login.html`、`apps/accounts/forms.py` の `EmailLoginForm`）。
- **なぜ②（根本原因)**: 本システムの認証は**パスワードレス（メールアドレスのみ）**。
  未知メールは利用者を自動作成し、弾くのは形式不正と無効化済み利用者のみ
  （`apps/accounts/views.py` `login_view`）。フェーズ1のテスト設計が一般的な
  「ユーザー名＋パスワード」認証を仮定し、実装仕様との突合を省略していた。

## 2. ODC分類

| # | 対象 | Activity | Trigger | Defect Type | Qualifier | Target | Impact |
|---|---|---|---|---|---|---|---|
| 1 | setup: PMO担当ログイン | System Test | Basic Coverage | Interface（画面契約の誤仮定） | Incorrect | **テスト資産**（auth.setup.ts） | Test Suite全体（PMO担当195件が未実行化） |
| 2 | TC-AUTH-052 誤ったパスワード | System Test | Basic Coverage | Interface | Incorrect | **テスト資産**（checks.ts） | Usability検証の欠落 |
| 3 | TC-AUTH-053 存在しない利用者ID | System Test | Coverage | Documentation（要件誤解によるテスト設計） | Missing | **テスト資産**（フェーズ1 CSV） | Security観点の空振り |
| 4 | TC-AUTH-057 パスワードマスク | System Test | Basic Coverage | Documentation | Extraneous（存在しない機能の検証） | **テスト資産**（フェーズ1 CSV） | なし |

**アプリケーション本体の不具合: この4件からは 0 件**（すべてテスト資産の欠陥）。

## 3. ユーザー価値・UI/UX評価（ログイン画面）

「使えない機能」ではない。むしろ意図的に摩擦を減らした設計で、UX上の長所が確認できる:

- メール1項目のみで、新人ペルソナの初回参入障壁が低い
- 失敗時に入力値を保持し「綴りを直してもう一度」と次の行動を案内する文言がある
- 体験用アドレス（pmo@example.com）を画面上に明示している

一方で、設計上の論点（改善提案候補・**未検証**、起票判断は利用者に委ねる）:

1. **未知メールの自動作成**: タイポでログインすると空の利用者が静かに作られ、
   「自分のデータが見えない」という混乱につながりうる。確認ステップまたは既存利用者の候補提示を検討。
2. **回数制限なしと画面に明記**: パスワードレスのため総当たりの対象は無いが、
   自動作成と組み合わさると大量のゴミ利用者を作れる。レート制限の要否は運用判断。

## 4. CSV改訂候補（フェーズ1成果物への反映）

パスワード前提のケースは仕様に合わせて差し替えが必要:

| テストID | 現行観点 | 差し替え案 |
|---|---|---|
| TC-AUTH-052 | 誤ったパスワード | 形式不正メールでのエラー表示と入力保持（今回テストで代替済み） |
| TC-AUTH-053 | 存在しない利用者IDの存在漏れ | 無効化済み利用者が汎用メッセージで弾かれること |
| TC-AUTH-056 | 連続失敗のロック | 自動作成の乱用防止方針の確認（運用判断） |
| TC-AUTH-057 | パスワードマスク | Enter送信の使い勝手（今回テストで代替済み） |

## 5. 今サイクルの修整内容

| ファイル | 変更 |
|---|---|
| e2e/lib/roles.ts | 既定利用者を `pmo@example.com` に変更、パスワードを任意化 |
| e2e/lib/checks.ts | ログイン検証をメールアドレス方式へ書き換え。適用不可の2観点は理由付きskipへ、2観点は仕様準拠の代替検証へ |
| e2e/auth.setup.ts | ロール別ログインをメールアドレス方式へ書き換え |

検証: `npx playwright test --project=role-pmo --project=anonymous -g "TC-AUTH-05"` を1回実行し、
setup成功（＝PMO担当ロールの解放）とログイン系の結果・証跡を確認する。

---

# サイクル2: PMO担当ロール解放後の機能テスト

対象: `--project=role-pmo` 全体（208件）。結果: Pass 30 / Fail 115 / Skip 63。

## 1. 根本原因（なぜ→なぜ）

- **観測**: Fail 115件が全件同一シグネチャ `locator('h1, h2, main').first()` 不可視。
- **なぜ①**: セレクタが DOM 順で最初に掴むのはサイドバーの `<h2 class="sb-hd">`
  （`templates/layouts/base.html:75`）。現在地以外のセクションは折りたたまれ不可視のため、
  可視の見出しがあっても `.first()` が不可視要素で確定してしまう。
- **なぜ②**: アサーション設計時に「最初のマッチ＝ページ見出し」と仮定し、
  レイアウトの実DOM（サイドバーが見出しより先に来る）と突合していなかった。

## 2. ODC分類

| # | 対象 | Activity | Trigger | Defect Type | Qualifier | Target | Impact |
|---|---|---|---|---|---|---|---|
| 1 | 表示検証の共通アサーション（115件に波及） | System Test | Coverage | Checking（不可視要素への可視性検証） | Incorrect | **テスト資産**（checks.ts pageLoads） | 表示系検証の全滅（偽陽性Fail） |

**アプリケーション本体の不具合: 0件。** むしろ UI/UX 上は肯定的な発見:
`base.html` は `<main aria-labelledby="page-heading">` ＋ `<h1 id="page-heading">` の
セマンティクスを備え、スクリーンリーダー・支援技術への配慮がある。

## 3. 修整

`e2e/lib/checks.ts` の見出し検証を可視要素に限定
（`h1:visible, h2:visible, main:visible`）。auth レイアウト（h2.auth-title のみ）にも適合する。

検証: `npx playwright test --project=role-pmo` を1回再実行 → Pass 30→132 / Fail 115→13。

## 4. 再実行後の残Fail 13件: オンボーディング画面（アプリ側の欠陥）

残る13件は初回テナント選択・初回案件選択に集中。テンプレート実体を確認した結果、
**アプリ側のUI/UX欠陥**と判定:

| # | 欠陥 | ODC | 根拠 |
|---|---|---|---|
| 2 | オンボーディング2画面に見出し要素（h1/h2）が無い | Function / Missing / **Target=Product** / Impact=Usability・Accessibility | ログイン画面には `h2.auth-title` があるのに、初回利用者が最初に見る2画面に見出しが無い。役割の提示がリード文のみで、WCAG 2.4.6（見出しとラベル）にも反する。新人ペルソナの「画面の役割が初見で分かる」という受入条件を満たさない |
| 3 | `onboarding_tenant.html:13` の `style="margin-top:0 0 14px"` は無効なCSS（margin-topに3値） | Assignment / Incorrect / **Target=Product** / Impact=Maintainability | 意図（下余白14px）が効いておらず、隣の onboarding_project.html の正しい記述（`margin:0 0 14px`）と食い違う |

### 修整（アプリ側テンプレート2ファイル）

- `templates/pages/onboarding_tenant.html`: `<h2 class="auth-title">参照するテナントを選ぶ</h2>` を追加、無効CSSを `margin:0 0 14px` へ修正
- `templates/pages/onboarding_project.html`: `<h2 class="auth-title">対象案件を選ぶ</h2>` を追加

検証: `npx playwright test --project=role-pmo -g "初回テナント選択|初回案件選択"` を1回実行 → 22 pass / 0 fail。

---

# サイクル3: 全ロール・フル実行と最終確認

準備: `e2e/seed_role_users.py` で6ロール＋無効化済み利用者を投入（パスワードレス認証のため
メールのみ・冪等）。CSV改訂候補6行を反映し、README の認証記述を実装仕様へ更新。

フル実行（全8プロジェクト・770件）: **Pass 489 / Fail 1 / Skip 280**（6.7分）。

## 残Fail 1件の分析と、それが導いた2つのテスト資産欠陥

| # | 欠陥 | ODC | 顛末 |
|---|---|---|---|
| 4 | 「参照のみロールの送信拒否確認」行を表示到達検査に誤ディスパッチ | Checking / Incorrect / **Target=テスト資産** | アプリは `/integrations/new/` への参照のみロールのアクセスを403で正しく拒否しており（`_require_tenant_admin`）、テストの誤分類が失敗として現れた。拒否確認専用チェックを追加 |
| 5 | 拒否のオラクルを「403のみ」と定義（7件が偽陽性Fail化） | Checking / Incorrect / **Target=テスト資産** | 案件系フォームは明示403ではなく**選択肢の空化**で防ぐ設計（`editable_projects_for` が参照のみロールに `projects.none()` を返す。要件#30「選べたのに保存できないを防ぐ」）。登録成功の3xxリダイレクトが発生しないことを拒否の判定に変更 |

**アプリ本体の認可欠陥: なし。** 参照のみロールは案件を1件も選択できず、偽装POSTも
choice バリデーションで弾かれるため、データは登録されない（一次確認済み）。

## UI/UX所見（改善提案候補・起票判断は利用者に委ねる）

1. 拒否方式が2系統（明示403／選択肢空化）あるのは設計意図に沿うが、
   参照のみロールでも**作成フォーム自体は開けて空の選択肢が表示される**。
   「選べない導線は出さない」という要件#30の思想を導線（ボタン表示）まで広げる余地がある。
2. サイクル1〜2の指摘（未知メール自動作成・オンボーディング見出し=修整済み）は前章参照。

## 最終検証

`npx playwright test --project=role-viewer -g "送信が拒否"` → **15 pass / 0 fail / 6 skip**。

## 最終サマリ（770件）

| 区分 | 件数 | 備考 |
|---|---|---|
| Pass | 490（射影値） | フル実行489＋修整済みTC-ADMIN-084。修整後の全体再実行は未実施 |
| Fail | 0 | 対象再実行で個別確認済み |
| Skip | 280 | 理由付き（要データ状態・要既存ID・要目視・手動）。フィクスチャ整備で漸減させる |

検出したアプリ本体の欠陥（全サイクル累計）: 2件（オンボーディング見出し欠如・無効CSS）— いずれも修整済み。
