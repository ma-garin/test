# 14 GUI 基本設計（3 画面版）

要件は `docs/13_GUI要件定義.md`。実装は `qa_sentinel/web/static/index.html`（単一 HTML）・`progress-views.js`（工程図）・`ds/`（デザインシステムの写し）。サーバーは `qa_sentinel/web/app.py`。

## 1. 構成

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

## 2. 画面レイアウト（`.app` 骨格）

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

## 3. 状態 → 表示の対応（`show()`）

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

## 4. 成果物の生成（画面 3）

| 成果物 | 生成元 | 形式 |
|---|---|---|
| `<ID>_テストの表.csv` | `casesOf(t)` | UTF-8 BOM、列: テストID,対象,目的,手順,期待される結果,重要度,根拠の版,状態（追加／失効） |
| `<ID>_実行手順.txt` | `stepsOf(t)` | 番号付き行 |
| `<ID>_完了報告.md` | `reportOf(t)` | 変更・プロジェクト・影響・追加/入替・金額・判断の記録（`decisions`）・根拠（`evidence` 段ごと）・成果物パスと作業ブランチ |

生成はブラウザ内（`dl(name, text, type)`）。サーバーに新規 API は不要。将来ファイル本文（`artifacts.report` 等）を出す場合は `GET /api/tasks/<id>/artifact?path=` を追加し、`docs/` 配下のみ許可する。

## 5. 「詳細」（`renderMore(t)`、開いたときだけ描画）

| カード | 内容 | 出典 |
|---|---|---|
| どこまで進んだか | スイムレーン工程図（AI／あなた／システム 3 レーン、菱形＝止まって聞く点、青ピル＝自動検査） | `progress-views.js`、`setFlow(t)` が `PV.state` を更新 |
| AI に使った金額（累計） | `$spent / 上限 $budget`、進捗バー、練習モード注記 | `t.session`, `t.plan.budget_usd` |
| AI に任せる範囲 | AI がやる／あなたがやる／止まって聞く（4 段以上は「先頭 〜 末尾（N 段）」）、影響、作業ブランチ | `t.plan`, `t.impact`, `t.branch` |
| なぜそう判断したか | 段ごとの `evidence`（現在段は開く） | `t.evidence` |
| 記録 | `history`（段・状態・理由）と `decisions`（誰が何を）を時刻降順 | `t.history`, `t.decisions` |

## 6. 文言辞書（平易語）

| 辞書 | 役割 | 例 |
|---|---|---|
| `WHO[status]` | 状態バッジ・一覧の状態 | review→「あなたが OK する番」、running→「AI が作業中（待つだけ）」 |
| `NEXT[status]` | 一覧の「次にすること」 | handoff→「手順どおりにテストして、結果を渡す」 |
| `LABEL_PLAIN[phase]` | 段名 | test-design→「テストの表づくり」、regression-swap→「古いテストの入れ替え」 |
| `REASON_PLAIN(r)` | 停止・質問の理由 | `rejects_exceeded:test-plan`→「『影響の洗い出し』を 3 回やり直したので止めました」 |
| `MODES[k]` | 任せる範囲の説明 | M2→「準備まで AI、実行はあなた（おすすめ）」 |
| `SEV[status]` | バッジ色 | 人の番=high、stopped=critical、running=info、done=low |

## 7. スタイル方針

- 値はすべて `var(--*)`（`ds/tokens.css`）。px は幅・高さの寸法だけ（`token-exempt` を注記）
- `html, body { overflow: hidden }`、`.app { height: 100dvh }`。`.scroll`（一覧・body・開いた詳細）だけ `overflow: auto`
- 塗りボタン（`btn--primary`）は 1 画面に 1 つ（一覧の「開く／受け取る」は行ごとに許容）
- hover を持つ規則は必ず `:focus-visible` を対にする（検査項目）
- テーマは `data-theme`（system/light/dark）。工程図は CSS 変数を毎フレーム読み直すので追従する

## 8. 検証

| 何を | どうやって | 合格 |
|---|---|---|
| API と静的配信 | `tests/test_core.py::test_web_static_guide_and_demo` ほか | pytest 全通過 |
| デザイン準拠 | `check_design.py --tokens ds/tokens.css --pairs ds/contrast-pairs.md index.html guide.html` | NG=0 |
| 画面 | Playwright（`/opt/pw-browsers` の Chromium）で `docs/12_完成GUI.html` を 1600×900 撮影: 伝える／OK／テストする／完了／止まった／詳細開 | JS エラー 0、ページスクロールなし |
| 初見の理解 | Opus に「初めて触る人」役で 6 画面を見せ、ブロッカーを列挙させる | 0 件 |
| 全体 | `python scripts/verify.py` | `ALL GREEN` |

## 9. 変更履歴

| 日付 | 内容 | PR |
|---|---|---|
| 2026-09-30 | 3 画面化（受信箱・KPI・絞り込み・モーダル廃止、成果物 3 点、詳細に畳む） | #36 |
| 2026-09-30 | サイドバー復帰（伝える・進んでいるもの／完了して受け取る／使い方）、本書と 13 を追加 | 次 PR |
