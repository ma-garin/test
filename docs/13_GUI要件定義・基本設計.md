# 13 GUI 要件定義・基本設計（3 画面版）

対象: `qa_sentinel/web/static/index.html`（台帳の画面）。第 2 部が基本設計。全体の仕様は `docs/01_設計仕様.md`、進行表示は `docs/08_進行表示の設計.md`。

## 第 1 部 要件定義

## 1.1 目的

仕様が変わったとき、QA が **1 文を伝えるだけ** で AI がテストを作り直し、人は **止まったところで 1 つ判断** し、最後に **成果物を受け取る**。GUI はこの 3 動作だけを最短で行わせる。

| 決めたこと | 理由（2026-09-30 の指摘） |
|---|---|
| 1 画面 = 1 目的（伝える／判断する／受け取る） | 「1 画面に情報量が多すぎる」「初めて触る人が理解できない」 |
| 工程図・金額・根拠・記録は既定で隠す | AI エージェントなのだから成果物を簡単に得られるべき。運用情報は求めたときだけ |
| PC 専用、ページはスクロールしない | 縦長スクロールを嫌う。判断に要る内容はカード内でスクロール |
| yuki-aidd-kit デザインシステム準拠、`.app` 骨格（サイドバーあり） | 既存ツール群と操作感をそろえる。検査 `check_design.py` NG=0 |

## 1.2 利用者と前提

| 項目 | 内容 |
|---|---|
| 主利用者 | QA 担当（初見でも使える）。副: 開発者・PM が状況を見る |
| 環境 | Windows 11・PC ブラウザ（1366×768 以上）。`start.bat` で起動、`http://127.0.0.1:8790/` |
| 事前知識 | 不要。専門語（フェーズ名・gate・セッション）は画面に出さず平易語に置き換える |
| 台帳 | `tasks/<id>.json` が真実源。CLI と同じものを見る。5 秒ごとに再読込 |
| 名前 | 上のバーの「あなたの名前」（`localStorage`）。OK・回答・承認はこの名前で記録。未入力なら送信を止めて入力を促す |

## 1.3 画面と機能要件

### 3.1 画面 1「伝える」（`#`）

| ID | 要件 |
|---|---|
| R1-1 | 入力欄 1 つ（何が変わったか）と「AI に任せる」ボタン 1 つを最上部に置く |
| R1-2 | 「まず例のデモで見る（請求なし）」で図書館デモを 1 クリックで開始する（`/api/demo`） |
| R1-3 | AI に任せる範囲（M1〜M8）・上限金額・対象プロジェクトは「詳しい設定」に畳む。既定 M2 / $5 / library-loan |
| R1-4 | 「いま進んでいるもの」一覧: 完了以外を、あなたの番 → その他 の順で並べる。1 行 = 題名・ID・状態・次にすること・ボタン（開く／様子を見る） |
| R1-5 | 入力なし・名前なし・API エラーは入力欄直下の赤帯に日本語で出し、送信しない |

### 3.2 画面 2「待つ／答える」（`#task/<id>`）

| ID | 要件 |
|---|---|
| R2-1 | 最上部は「いまやること」カード 1 枚。状態ごとに見出し・入力・主ボタンは次の 1 組だけ |
| R2-2 | 状態と表示: review「AI が『段』を作りました。下を見て、良ければ OK」＋メモ＋[OK][やり直してもらう]／handoff「『段』はあなたの番」＋結果＋ファイル＋[結果を渡す]／blocked 質問文＋答え＋[答える]／paused「問題なければ承認」＋[承認する]／running「作っています。待つだけ」／stopped 理由（日本語）＋[始め直す] |
| R2-3 | カードの下に、その判断に要る内容だけを出す: review=当該段の下書き（表・手順・本文）、handoff=実行手順＋確かめるテストの表、paused=テストの表、stopped/running=最新の下書き |
| R2-4 | テストの表は ID・対象（＋目的）・手順・期待される結果・重要度。追加はバッジ＋緑地、失効は取り消し線 |
| R2-5 | 「詳細」（`<details>`、既定閉）に工程図（スイムレーン）・AI に使った金額・任せる範囲・なぜそう判断したか（根拠）・記録（履歴と判断）を入れる |
| R2-6 | 送信中はボタンを `aria-busy` にし「処理中…」。成功はトースト、失敗は再送ボタン付きエラー |
| R2-7 | 「始め直す」は画面 1 に戻り、変更文とプロジェクトを入力欄に入れる |

### 3.3 画面 3「受け取る」（`#task/<id>`、status=done）

| ID | 要件 |
|---|---|
| R3-1 | 成果物 3 点を 1 クリックで保存: テストの表（CSV, BOM 付き）・実行手順（txt）・完了報告（md: 変更・影響・追加/失効・金額・判断の記録・根拠・成果物パス） |
| R3-2 | 下にテストの表を表示（R2-4 と同じ） |
| R3-3 | サイドバー「完了して受け取る」（`#done`）で完了したものだけを一覧し、「受け取る」で開く |

### 3.4 共通

| ID | 要件 |
|---|---|
| RC-1 | サイドバー 3 項目: 伝える・進んでいるもの（あなたの番の件数バッジ）／完了して受け取る／使い方（別タブ）。折りたたみ可 |
| RC-2 | 上のバー: 製品名・一言説明・あなたの名前・「練習モード（請求なし）」バッジ・テーマ（システム／ライト／ダーク） |
| RC-3 | 文言は平易語のみ。段名は `LABEL_PLAIN`（影響の洗い出し／テストの表づくり…）、停止理由は `REASON_PLAIN` で日本語化。内部コード（`rejects_exceeded:` 等）を出さない |
| RC-4 | AI は承認欄を埋めない。承認・OK・回答は人の名前で `decisions` に記録される（サーバー側で保証） |

## 1.4 非機能要件

| 項目 | 要件 | 確認方法 |
|---|---|---|
| 縦スクロール | ページは `overflow:hidden`。スクロールは表・下書き・詳細のカード内だけ | Playwright 1600×900 で 6 画面撮影 |
| デザイン | 色・余白・文字は `ds/tokens.css` の `var(--*)` のみ。hover には focus-visible 対 | `check_design.py` NG=0 |
| 依存 | 外部ライブラリなし。`ds/`（tokens/components/layout/icons/feedback）と `progress-views.js` だけ | `scripts/verify.py` |
| 単体配布 | `docs/12_完成GUI.html` にサーバーなしで動く完成形を生成（ページ内台帳） | `scripts/build_gui_standalone.py` |
| 応答 | 台帳の再読込 5 秒。操作後は即時再読込 | 目視 |
| アクセシビリティ | ボタンは `<button type=button>`、アイコンは `aria-hidden`、canvas に `role=img` と `aria-label` | 検査項目 |

## 1.5 受け入れ基準

| # | 基準 |
|---|---|
| A1 | 初見の人が説明なしで「デモを始め → OK → 結果を渡す → 承認 → CSV を保存」まで到達できる（Opus「初めて触る人」役の指摘 0 件） |
| A2 | `python3 -m pytest -q tests` 全通過、`scripts/verify.py` が `ALL GREEN`、`check_design.py` NG=0 |
| A3 | 6 画面（伝える／OK／テストする／完了／止まった／詳細開）で JS エラー 0、ページスクロールなし |
| A4 | 画面上に内部コード・英語のフェーズ名が出ない |

## 1.6 対象外

- スマートフォン・タブレット表示
- 画面上でテストの表を直接編集する（ファイルを直して OK する運用。将来課題）
- 通知（LINE・メール）、複数人同時編集の競合制御

---


## 第 2 部 基本設計

要件は第 1 部。実装は `qa_sentinel/web/static/index.html`（単一 HTML）・`progress-views.js`（工程図）・`ds/`（デザインシステムの写し）。サーバーは `qa_sentinel/web/app.py`。

## 2.1 構成

```
ブラウザ                                   サーバー（app.py, 標準ライブラリのみ）        台帳
┌──────────────────────────────┐   GET /api/meta, /api/tasks, /api/tasks/<id>   ┌────────────┐
│ index.html（画面 1/2/3）      │ ─────────────────────────────────────────────→ │ tasks/*.json│
│  ├ ds/tokens|components|layout│   POST /api/run|demo|review|submit|answer|approve│ （真実源）  │
│  ├ ds/icons.js, feedback.js   │ ─────────────────────────────────────────────→ │            │
│  └ progress-views.js（詳細内） │                 ← JSON（Task.to_dict()）        └────────────┘
└──────────────────────────────┘
docs/12_完成GUI.html = index.html + ds をインライン + gui_sim.js（fetch を差し替えたページ内台帳）
```

- 画面はハッシュで切り替える: `#`（伝える・進んでいるもの）／`#done`（完了）／`#task/<id>`（待つ／答える／受け取る）
- 状態は `tasks`（一覧）・`cur`（表示中 ID）・`view`（live|done）・`meta`（phases/labels/presets）の 4 つ。5 秒ごとに `load()`

## 2.2 画面レイアウト（`.app` 骨格）

```
┌ globalbar: [QS] qa-sentinel  一言説明        あなたの名前[   ]  [練習モード] [テーマ] ┐
├ sidebar ──┬─ content（中央 960px、縦は 100dvh に収める） ─────────────────────────┤
│ 伝える・  │  画面 1                         画面 2/3                                │
│  進んで   │  ┌ 何が変わりましたか ┐          ┌ ← 戻る  題名             [状態バッジ] ┐ │
│  いるもの │  │ [入力欄    ][AI に任せる]│     ├ いまやること（act）1 枚 ─────────────┤ │
│ 完了して  │  │ 例のデモ／詳しい設定 ▸ │      │  見出し・入力・主ボタン               │ │
│  受け取る │  └───────────────────┘      ├ その判断に要る内容（body, 内部スクロール）┤ │
│ 使い方    │  ┌ いま進んでいるもの ─┐     │  表／手順／本文／成果物 3 点             │ │
│           │  │ 行 × N（内部スクロール）│  └ ▸ 詳細（工程図・金額・根拠・記録）──────┘ │
└───────────┴──────────────────────────────────────────────────────────────────┘
```

| 領域 | 要素 | 補足 |
|---|---|---|
| `#v-list .ask` | `#nl` textarea、`#go`、`#demo-go`、`details.adv`（`#r-mode` select／`#r-budget`／`#r-project`／`#r-preview`）、`#r-err` | `view==='done'` のとき非表示 |
| `#v-list #now` | `#now-body` に `.now-item` × N | 見出しは view で切替 |
| `#v-detail` | `.head`（戻る・`#ttl`・`#d-badge`）、`#act`、`#body`、`details#more > #more-body` | `show()` が初回だけ骨格を作り、以後は中身だけ差し替え |

## 2.3 状態 → 表示の対応（`show()`）

| status | act の左線色 | 見出し | 入力 | 主ボタン → API | body |
|---|---|---|---|---|---|
| review | 橙 | AI が「段」を作りました。下を見て、良ければ OK | `#note` | `#ok` → `/api/review ok=1`、`#rej` → `ok=0` | `draftOf(t)`: 当該段の下書き |
| handoff | 橙 | 「段」はあなたの番です。下の手順でテストして、結果を渡す | `#note`、`#file` | `#sub` → `/api/submit` | 実行手順（`draft['test-implementation'].steps`、`<task>` を ID に置換）＋ `casesTable(t)` |
| blocked | 橙 | AI からの質問 ＋ 質問文（`REASON_PLAIN`） | `#note` | `#ans` → `/api/answer` | `draftOf(t)` |
| paused | 緑 | AI の作業は終わりました。問題なければ承認 | — | `#ap` → `/api/approve` | `casesTable(t)` |
| running | 青 | AI が「段」を作っています。待つだけ | — | — | `draftOf(t)` |
| stopped | 赤 | 止まりました ＋ 理由（日本語） | — | `#redo` → 画面 1 へ（入力欄に変更文を復元） | `draftOf(t)` |
| done | 緑 | 完了。成果物を受け取ってください | — | `#dl-csv`／`#dl-steps`／`#dl-rep`（クライアント生成・Blob 保存） | `casesTable(t)` |

- `draftOf(t)`: `t.draft[t.phase]` を cases → steps → text の順で判定。無ければ成果物パスを callout で示す
- `casesTable(t)`: `t.draft['test-design'].cases`。列は ID・対象（＋目的）・手順・期待される結果・重要度。`state=new` は緑地、`retired` は取り消し線
- 送信は `wire(bid, fn, errMsg)` で共通化: 名前チェック → `aria-busy` → API → `done()`（トースト → `load()` → `show(id, true)`）

## 2.4 成果物の生成（画面 3）

| 成果物 | 生成元 | 形式 |
|---|---|---|
| `<ID>_テストの表.csv` | `casesOf(t)` | UTF-8 BOM、列: テストID,対象,目的,手順,期待される結果,重要度,根拠の版,状態（追加／失効） |
| `<ID>_実行手順.txt` | `stepsOf(t)` | 番号付き行 |
| `<ID>_完了報告.md` | `reportOf(t)` | 変更・プロジェクト・影響・追加/入替・金額・判断の記録（`decisions`）・根拠（`evidence` 段ごと）・成果物パスと作業ブランチ |

生成はブラウザ内（`dl(name, text, type)`）。サーバーに新規 API は不要。将来ファイル本文（`artifacts.report` 等）を出す場合は `GET /api/tasks/<id>/artifact?path=` を追加し、`docs/` 配下のみ許可する。

## 2.5 「詳細」（`renderMore(t)`、開いたときだけ描画）

| カード | 内容 | 出典 |
|---|---|---|
| どこまで進んだか | スイムレーン工程図（AI／あなた／システム 3 レーン、菱形＝止まって聞く点、青ピル＝自動検査） | `progress-views.js`、`setFlow(t)` が `PV.state` を更新 |
| AI に使った金額（累計） | `$spent / 上限 $budget`、進捗バー、練習モード注記 | `t.session`, `t.plan.budget_usd` |
| AI に任せる範囲 | AI がやる／あなたがやる／止まって聞く（4 段以上は「先頭 〜 末尾（N 段）」）、影響、作業ブランチ | `t.plan`, `t.impact`, `t.branch` |
| なぜそう判断したか | 段ごとの `evidence`（現在段は開く） | `t.evidence` |
| 記録 | `history`（段・状態・理由）と `decisions`（誰が何を）を時刻降順 | `t.history`, `t.decisions` |

## 2.6 文言辞書（平易語）

| 辞書 | 役割 | 例 |
|---|---|---|
| `WHO[status]` | 状態バッジ・一覧の状態 | review→「あなたが OK する番」、running→「AI が作業中（待つだけ）」 |
| `NEXT[status]` | 一覧の「次にすること」 | handoff→「手順どおりにテストして、結果を渡す」 |
| `LABEL_PLAIN[phase]` | 段名 | test-design→「テストの表づくり」、regression-swap→「古いテストの入れ替え」 |
| `REASON_PLAIN(r)` | 停止・質問の理由 | `rejects_exceeded:test-plan`→「『影響の洗い出し』を 3 回やり直したので止めました」 |
| `MODES[k]` | 任せる範囲の説明 | M2→「準備まで AI、実行はあなた（おすすめ）」 |
| `SEV[status]` | バッジ色 | 人の番=high、stopped=critical、running=info、done=low |

## 2.7 スタイル方針

- 値はすべて `var(--*)`（`ds/tokens.css`）。px は幅・高さの寸法だけ（`token-exempt` を注記）
- `html, body { overflow: hidden }`、`.app { height: 100dvh }`。`.scroll`（一覧・body・開いた詳細）だけ `overflow: auto`
- 塗りボタン（`btn--primary`）は 1 画面に 1 つ（一覧の「開く／受け取る」は行ごとに許容）
- hover を持つ規則は必ず `:focus-visible` を対にする（検査項目）
- テーマは `data-theme`（system/light/dark）。工程図は CSS 変数を毎フレーム読み直すので追従する

## 2.8 検証

| 何を | どうやって | 合格 |
|---|---|---|
| API と静的配信 | `tests/test_core.py::test_web_static_guide_and_demo` ほか | pytest 全通過 |
| デザイン準拠 | `check_design.py --tokens ds/tokens.css --pairs ds/contrast-pairs.md index.html guide.html` | NG=0 |
| 画面 | Playwright（`/opt/pw-browsers` の Chromium）で `docs/12_完成GUI.html` を 1600×900 撮影: 伝える／OK／テストする／完了／止まった／詳細開 | JS エラー 0、ページスクロールなし |
| 初見の理解 | Opus に「初めて触る人」役で 6 画面を見せ、ブロッカーを列挙させる | 0 件 |
| 全体 | `python scripts/verify.py` | `ALL GREEN` |

## 2.9 変更履歴

| 日付 | 内容 | PR |
|---|---|---|
| 2026-09-30 | 3 画面化（受信箱・KPI・絞り込み・モーダル廃止、成果物 3 点、詳細に畳む） | #36 |
| 2026-09-30 | サイドバー復帰（伝える・進んでいるもの／完了して受け取る／使い方）、本書を追加 | #37 |
| 2026-09-30 | 要件定義と基本設計を 1 文書に統合 | 次 PR |
