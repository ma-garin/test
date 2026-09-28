"""build.py — library-loan.html を組み立てる（事例のソースは app.css / app.js。部品はキットの実物を毎回コピー）。

使い方（キットのルートで）: python3 01_利用者向け資料/90_サンプル/図書貸出/build.py
tokens.css / ui/components.css / ui/layout.css / components/icons.js / feedback.js を変えたら再実行して同期する。
"""
import sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parents[2]; S = HERE; OUT = HERE / 'library-loan.html'
rd = lambda p: pathlib.Path(p).read_text(encoding='utf-8')
tokens = rd(KIT/'02_共通/ひな形/tokens.css'); comps = rd(KIT/'02_共通/ひな形/ui/components.css'); layout = rd(KIT/'02_共通/ひな形/ui/layout.css')
icons = rd(KIT/'02_共通/ひな形/components/icons.js'); feedback = rd(KIT/'02_共通/ひな形/components/feedback.js'); app_css = rd(S/'app.css'); app_js = rd(S/'app.js')
html = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>図書貸出管理 — 社内図書館（モック）</title>
<!--
  library-loan.html — 社内図書館の貸出管理（Excel → Web）の HTML モック。
  single-html-tool 規約: 単一ファイル・外部 CDN なし・データは localStorage。
  見た目は AIDD Kit のデザインシステムをそのまま同梱（順に tokens.css → ui/components.css → ui/layout.css）。
  操作フィードバック（トースト・確認・空状態）は components/feedback.js、アイコンは components/icons.js。
  未実装（本実装で対応）: Excel の取り込み・利用者の追加・認証・サーバ保存。
  このファイルは 01_利用者向け資料/90_サンプル/図書貸出/build.py が app.css / app.js とキットの実物から組み立てた生成物。
  直接編集せず、app.css / app.js を直して build.py を再実行する。
-->
<style>
/* ===== 1/4 templates/tokens.css（値の唯一の真実源。ここをコピーしただけ） ===== */
{tokens}
/* ===== 2/4 02_共通/ひな形/ui/components.css ===== */
{comps}
/* ===== 3/4 02_共通/ひな形/ui/layout.css ===== */
{layout}
/* ===== 4/4 このモック固有 ===== */
{app_css}
</style>
</head>
<body>
<div class="app" id="app">
  <header class="app-globalbar">
    <button class="btn btn--ghost" id="menu" type="button" aria-label="メニューを開く"><span data-icon="grid-view" data-icon-size="18"></span></button>
    <span class="brand">社内図書館 貸出管理</span>
    <span class="spacer"></span>
    <span class="muted">総務部 図書係</span>
    <button class="btn btn--ghost" id="theme" type="button" aria-label="テーマを切り替える"><span data-icon="settings" data-icon-size="16"></span><span class="label-text">テーマ</span></button>
  </header>
  <div class="app-body">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-head"><span class="brand">メニュー</span><button class="btn btn--ghost" id="collapse" type="button" aria-label="サイドバーを折りたたむ">‹</button></div>
      <nav aria-label="主要">
        <a href="#dashboard"><span class="icon" data-icon="home" data-icon-size="18"></span><span class="label-text">ダッシュボード</span></a>
        <a href="#books"><span class="icon" data-icon="table-view" data-icon-size="18"></span><span class="label-text">蔵書</span></a>
        <a href="#loans"><span class="icon" data-icon="history" data-icon-size="18"></span><span class="label-text">貸出中</span></a>
        <a href="#users"><span class="icon" data-icon="person" data-icon-size="18"></span><span class="label-text">利用者</span></a>
        <a href="#settings"><span class="icon" data-icon="settings" data-icon-size="18"></span><span class="label-text">設定</span></a>
      </nav>
    </aside>
    <div class="maincol">
      <header class="app-topbar">
        <div><div class="breadcrumb"><a href="#dashboard">ホーム</a><span>/</span><span id="crumb">ダッシュボード</span></div><h1 id="page-title">ダッシュボード</h1></div>
        <span class="spacer"></span>
        <button class="btn btn--primary" id="export-csv" type="button"><span data-icon="download" data-icon-size="16"></span>貸出中を CSV に書き出す</button>
      </header>
      <main class="app-content">

        <section class="view" id="view-dashboard" data-title="ダッシュボード">
          <div class="callout callout--critical" id="overdue-callout" hidden><span data-icon="circle-alert" data-icon-size="16"></span><span></span></div>
          <div class="kpi-row">
            <div class="card kpi"><span class="kpi-label">蔵書数</span><span class="kpi-value" id="kpi-books">0</span><span class="kpi-delta">冊</span></div>
            <div class="card kpi"><span class="kpi-label">貸出中</span><span class="kpi-value" id="kpi-out">0</span><span class="kpi-delta">冊</span></div>
            <div class="card kpi"><span class="kpi-label">延滞</span><span class="kpi-value" id="kpi-over">0</span><span class="kpi-delta down">要連絡</span></div>
            <div class="card kpi"><span class="kpi-label">今日が返却期限</span><span class="kpi-value" id="kpi-today">0</span><span class="kpi-delta">冊</span></div>
          </div>
          <div class="card">
            <h3>返却期限が近い順</h3>
            <div class="table-wrap"><table class="table">
              <thead><tr><th>書名</th><th>借りている人</th><th class="num">返却期限</th><th>状態</th></tr></thead>
              <tbody id="recent-body"></tbody>
            </table></div>
          </div>
        </section>

        <section class="view" id="view-books" data-title="蔵書">
          <div class="toolbar">
            <input class="input" id="book-q" type="search" placeholder="書名・著者・ID で検索" aria-label="蔵書を検索">
            <span class="seg" id="book-seg" role="group" aria-label="状態で絞り込む"><span class="active" data-v="all">すべて</span><span data-v="available">貸出可</span><span data-v="out">貸出中</span><span data-v="overdue">延滞</span></span>
            <span class="spacer"></span>
            <button class="btn" type="button" id="book-add"><span data-icon="add" data-icon-size="16"></span>蔵書を追加する</button>
          </div>
          <div class="card">
            <div id="books-table-wrap" class="table-wrap"><table class="table">
              <thead><tr><th>ID</th><th>書名</th><th>著者</th><th>分類</th><th>状態</th><th>操作</th></tr></thead>
              <tbody id="books-body"></tbody>
            </table></div>
            <div id="books-empty" hidden></div>
            <div class="pagebar"><span class="muted" id="books-range"></span><span class="pager" id="books-pager"></span></div>
          </div>
        </section>

        <section class="view" id="view-loans" data-title="貸出中">
          <div class="card">
            <h3>貸出中の一覧（返却期限順）</h3>
            <div id="loans-table-wrap" class="table-wrap"><table class="table">
              <thead><tr><th>貸出 ID</th><th>書名</th><th>利用者</th><th class="num">貸出日</th><th class="num">返却期限</th><th>状態</th><th>操作</th></tr></thead>
              <tbody id="loans-body"></tbody>
            </table></div>
            <div id="loans-empty" hidden></div>
          </div>
        </section>

        <section class="view" id="view-users" data-title="利用者">
          <div class="card">
            <h3>利用者と貸出状況</h3>
            <div class="table-wrap"><table class="table">
              <thead><tr><th>ID</th><th>氏名</th><th>部署</th><th class="num">貸出冊数 / 上限</th><th>状態</th></tr></thead>
              <tbody id="users-body"></tbody>
            </table></div>
            <p class="hint">利用者の追加・編集は本実装で対応します（モックでは表示のみ）。</p>
          </div>
        </section>

        <section class="view" id="view-settings" data-title="設定">
          <div class="settings-grid">
            <div class="card">
              <h3>貸出ルール</h3>
              <div class="form-grid">
                <div class="field"><label for="set-days">貸出期間（日）</label><input class="input" id="set-days" type="number" min="1" max="60"></div>
                <div class="field"><label for="set-max">1 人あたりの冊数上限</label><input class="input" id="set-max" type="number" min="1" max="10"></div>
                <div><button class="btn btn--primary" id="set-save" type="button"><span data-icon="save" data-icon-size="16"></span>設定を保存する</button></div>
              </div>
            </div>
            <div class="card">
              <h3>Excel から取り込む</h3>
              <div class="import-box"><span data-icon="description" data-icon-size="28"></span><p>貸出台帳（.xlsx）をここにドロップ、または選択</p><button class="btn" id="set-import" type="button">ファイルを選ぶ</button></div>
              <p class="hint">列の対応（書名・著者・分類・利用者・貸出日・返却期限）は取り込み時に確認します。</p>
            </div>
            <div class="card">
              <h3>動作確認用</h3>
              <div class="form-grid">
                <div class="filter-row"><span class="toggle" id="set-fail" role="switch" aria-checked="false" tabindex="0"><i></i></span><span>保存系の操作を失敗させる（失敗時の表示を確認する）</span></div>
                <div><button class="btn btn--danger" id="set-reset" type="button"><span data-icon="refresh" data-icon-size="16"></span>サンプルデータに戻す</button></div>
                <p class="version" id="version"></p>
              </div>
            </div>
          </div>
        </section>

      </main>
    </div>
  </div>
</div>

<div class="modal-backdrop" id="lend-modal">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="lend-title">
    <div class="modal-head"><h3 id="lend-title">貸し出す</h3><button class="modal-close" type="button" aria-label="閉じる">×</button></div>
    <div class="modal-body form-grid">
      <div class="field"><label for="lend-user">利用者</label><select class="select" id="lend-user"></select><span class="field-err-text" id="lend-err" hidden><span data-icon="circle-alert" data-icon-size="14"></span><span></span></span></div>
      <div class="field"><label for="lend-due">返却期限</label><input class="input" id="lend-due" type="date"><span class="hint">貸出期間は設定画面で変更できます</span></div>
    </div>
    <div class="modal-foot"><button class="btn btn--ghost modal-close" type="button">やめる</button><button class="btn btn--primary" id="lend-submit" type="button">貸し出す</button></div>
  </div>
</div>

<script>
/* ===== templates/components/icons.js（そのまま同梱） ===== */
{icons}
</script>
<script>
/* ===== templates/components/feedback.js（そのまま同梱） ===== */
{feedback}
</script>
<script>
{app_js}
</script>
</body>
</html>
"""
OUT.write_text(html, encoding='utf-8'); print(OUT, sum(1 for _ in html.split('\n')), 'lines')
