/* ── library-loan アプリ本体（モック。データはブラウザ内 localStorage。Excel 取り込みは未実装） ── */
(function () {
  'use strict';
  const KEY = 'library-loan:data';   // {appName}:{dataType}。schema にバージョン
  const SCHEMA = 1;
  const LOAN_DAYS = 14;
  const today = () => new Date(new Date().toDateString());
  const fmt = (d) => d.toISOString().slice(0, 10);
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

  function seed() {
    const t = today();
    const books = [
      ['B-001', 'ソフトウェアテスト技法練習帳', '梅津 正洋 ほか', '技術'], ['B-002', 'アジャイルサムライ', 'Jonathan Rasmusson', '技術'],
      ['B-003', 'リーダブルコード', 'Dustin Boswell', '技術'], ['B-004', 'ISTQB Foundation シラバス解説', '日本ソフトウェアテスト協会', '資格'],
      ['B-005', 'SCRUM BOOT CAMP THE BOOK', '西村 直人 ほか', '技術'], ['B-006', 'ファシリテーションの教科書', '吉田 素文', 'ビジネス'],
      ['B-007', '失敗の科学', 'マシュー・サイド', 'ビジネス'], ['B-008', 'プロジェクトマネジメント標準 PMBOK 入門', 'PMI 日本支部', '資格'],
      ['B-009', 'Playwright 実践ガイド', '（社内資料）', '技術'], ['B-010', 'ノンデザイナーズ・デザインブック', 'Robin Williams', 'デザイン'],
      ['B-011', 'エンジニアのためのマネジメント入門', '佐藤 大典', 'ビジネス'], ['B-012', '情報処理安全確保支援士 教科書', '（資格）', '資格'],
    ].map(([id, title, author, category]) => ({ id, title, author, category }));
    const users = [
      { id: 'U-01', name: '田中 一郎', dept: '品質保証部' }, { id: 'U-02', name: '佐藤 花子', dept: '開発 2 課' },
      { id: 'U-03', name: '鈴木 次郎', dept: '開発 1 課' }, { id: 'U-04', name: '高橋 美咲', dept: '企画部' },
    ];
    const loans = [
      { id: 'L-1001', bookId: 'B-002', userId: 'U-02', out: fmt(addDays(t, -20)), due: fmt(addDays(t, -6)) },   // 延滞
      { id: 'L-1002', bookId: 'B-004', userId: 'U-01', out: fmt(addDays(t, -13)), due: fmt(addDays(t, 1)) },    // 明日
      { id: 'L-1003', bookId: 'B-007', userId: 'U-04', out: fmt(addDays(t, -3)), due: fmt(addDays(t, 11)) },
      { id: 'L-1004', bookId: 'B-009', userId: 'U-03', out: fmt(addDays(t, -14)), due: fmt(t) },              // 今日
    ];
    return { schema: SCHEMA, books, users, loans, returned: [], settings: { loanDays: LOAN_DAYS, maxBooks: 3, failSim: false } };
  }
  function load() {
    try { const d = JSON.parse(localStorage.getItem(KEY) || 'null'); if (d && d.schema === SCHEMA) return d; } catch (e) { /* 壊れていれば作り直す */ }
    const d = seed(); save(d); return d;
  }
  function save(d) { try { localStorage.setItem(KEY, JSON.stringify(d)); } catch (e) { /* private モード等。メモリ上で続行 */ } }

  let db = load();
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  const loanOf = (bookId) => db.loans.find((l) => l.bookId === bookId);
  const userOf = (id) => db.users.find((u) => u.id === id) || { name: '（不明）', dept: '' };
  const bookOf = (id) => db.books.find((b) => b.id === id) || { title: '（不明）' };
  const daysLeft = (due) => Math.round((new Date(due) - today()) / 86400000);
  const status = (bookId) => { const l = loanOf(bookId); if (!l) return 'available'; return daysLeft(l.due) < 0 ? 'overdue' : 'out'; };
  const badge = (s) => {
    const map = { available: ['badge-low', '貸出可'], out: ['badge-info', '貸出中'], overdue: ['badge-critical', '延滞'] };
    const b = el('span', 'badge ' + map[s][0], map[s][1]); return b;
  };
  const dueCell = (due) => {
    const n = daysLeft(due); const td = el('td', 'num');
    td.textContent = due + (n < 0 ? `（${-n} 日超過）` : n === 0 ? '（今日）' : n <= 2 ? `（あと ${n} 日）` : '');
    if (n < 0) td.classList.add('due-over'); else if (n <= 2) td.classList.add('due-soon');
    return td;
  };

  // ── 失敗の再現（設定でONにすると保存系の操作が失敗する。失敗時の見え方を確認するため） ──
  function tryWrite(fn) {
    return new Promise((resolve, reject) => setTimeout(() => (db.settings.failSim ? reject(new Error('サーバが応答しませんでした。')) : resolve(fn())), 500));
  }

  // ── ルーティング（hash） ──
  const VIEWS = ['dashboard', 'books', 'loans', 'users', 'settings'];
  function route() {
    const v = (location.hash || '#dashboard').slice(1);
    const name = VIEWS.includes(v) ? v : 'dashboard';
    $$('.view').forEach((s) => s.classList.toggle('active', s.id === 'view-' + name));
    $$('.sidebar nav a').forEach((a) => { if (a.getAttribute('href') === '#' + name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    $('#app').classList.toggle('is-settings', name === 'settings');
    $('#page-title').textContent = $('#view-' + name).dataset.title;
    $('#crumb').textContent = $('#view-' + name).dataset.title;
    $('#sidebar').classList.remove('open');
    render();
  }

  // ── 各画面の描画 ──
  function render() { renderDashboard(); renderBooks(); renderLoans(); renderUsers(); renderSettings(); }

  function renderDashboard() {
    const out = db.loans.length, over = db.loans.filter((l) => daysLeft(l.due) < 0).length, dueToday = db.loans.filter((l) => daysLeft(l.due) === 0).length;
    $('#kpi-books').textContent = String(db.books.length);
    $('#kpi-out').textContent = String(out);
    $('#kpi-over').textContent = String(over);
    $('#kpi-today').textContent = String(dueToday);
    const co = $('#overdue-callout'); co.hidden = over === 0;
    if (over) co.querySelector('span:last-child').textContent = `延滞が ${over} 件あります。「貸出中」で確認して、利用者に連絡してください。`;
    const tb = $('#recent-body'); tb.replaceChildren();
    db.loans.slice().sort((a, b) => a.due.localeCompare(b.due)).slice(0, 5).forEach((l) => {
      const tr = el('tr'); tr.append(el('td', null, bookOf(l.bookId).title), el('td', null, userOf(l.userId).name), dueCell(l.due));
      const td = el('td'); td.append(badge(status(l.bookId))); tr.append(td); tb.append(tr);
    });
  }

  let bookFilter = 'all', bookQuery = '', page = 1; const PAGE = 8;
  function renderBooks() {
    const q = bookQuery.trim().toLowerCase();
    let rows = db.books.filter((b) => !q || b.title.toLowerCase().includes(q) || b.author.toLowerCase().includes(q) || b.id.toLowerCase().includes(q));
    if (bookFilter !== 'all') rows = rows.filter((b) => status(b.id) === bookFilter);
    const total = rows.length, pages = Math.max(1, Math.ceil(total / PAGE)); if (page > pages) page = pages;
    const slice = rows.slice((page - 1) * PAGE, page * PAGE);
    const tb = $('#books-body'); tb.replaceChildren();
    slice.forEach((b) => {
      const s = status(b.id); const tr = el('tr');
      tr.append(el('td', 'mono', b.id), el('td', null, b.title), el('td', null, b.author), el('td', null, b.category));
      const td = el('td'); td.append(badge(s)); tr.append(td);
      const act = el('td'); const wrap = el('div', 'row-actions');
      if (s === 'available') { const btn = el('button', 'btn btn--primary', '貸出する'); btn.type = 'button'; btn.addEventListener('click', () => openLend(b)); wrap.append(btn); }
      else { const btn = el('button', 'btn', '返却する'); btn.type = 'button'; btn.addEventListener('click', () => doReturn(loanOf(b.id))); wrap.append(btn); }
      act.append(wrap); tr.append(act); tb.append(tr);
    });
    const empty = $('#books-empty'); const table = $('#books-table-wrap');
    if (total === 0) {
      table.hidden = true; empty.hidden = false;
      empty.replaceChildren(window.Feedback.emptyState(q || bookFilter !== 'all'
        ? { title: '条件に一致する蔵書がありません', description: '検索語や絞り込みを変えてください。', icon: 'search-off', action: { label: '絞り込みを解除する', onClick: () => { bookQuery = ''; bookFilter = 'all'; $('#book-q').value = ''; setSeg('all'); page = 1; renderBooks(); } } }
        : { title: 'まだ蔵書が登録されていません', description: '設定画面から Excel を取り込むか、蔵書を追加してください。', icon: 'add', action: { label: '蔵書を追加する', onClick: () => window.Feedback.info('モックでは追加は動きません') } }));
    } else { table.hidden = false; empty.hidden = true; }
    $('#books-range').textContent = total ? `${(page - 1) * PAGE + 1}–${Math.min(page * PAGE, total)} / ${total} 件` : '0 件';
    const pg = $('#books-pager'); pg.replaceChildren();
    const mk = (label, p, active) => { const s = el('span', 'page' + (active ? ' active' : ''), label); s.setAttribute('role', 'button'); s.tabIndex = 0; s.addEventListener('click', () => { page = p; renderBooks(); }); return s; };
    pg.append(mk('‹', Math.max(1, page - 1)));
    for (let i = 1; i <= pages; i++) pg.append(mk(String(i), i, i === page));
    pg.append(mk('›', Math.min(pages, page + 1)));
  }
  function setSeg(v) { bookFilter = v; $$('#book-seg span').forEach((s) => s.classList.toggle('active', s.dataset.v === v)); }

  function renderLoans() {
    const tb = $('#loans-body'); tb.replaceChildren();
    const rows = db.loans.slice().sort((a, b) => a.due.localeCompare(b.due));
    $('#loans-table-wrap').hidden = rows.length === 0; const empty = $('#loans-empty'); empty.hidden = rows.length !== 0;
    if (!rows.length) empty.replaceChildren(window.Feedback.emptyState({ title: '貸出中の本はありません', description: '蔵書画面から「貸出する」で貸し出せます。', icon: 'check-circle', action: { label: '蔵書を見る', onClick: () => { location.hash = '#books'; } } }));
    rows.forEach((l) => {
      const tr = el('tr'); const u = userOf(l.userId);
      tr.append(el('td', 'mono', l.id), el('td', null, bookOf(l.bookId).title), el('td', null, `${u.name}（${u.dept}）`), el('td', 'num', l.out), dueCell(l.due));
      const td = el('td'); td.append(badge(status(l.bookId))); tr.append(td);
      const act = el('td'); const btn = el('button', 'btn', '返却する'); btn.type = 'button'; btn.addEventListener('click', () => doReturn(l)); act.append(btn); tr.append(act); tb.append(tr);
    });
  }

  function renderUsers() {
    const tb = $('#users-body'); tb.replaceChildren();
    db.users.forEach((u) => {
      const n = db.loans.filter((l) => l.userId === u.id).length; const over = db.loans.filter((l) => l.userId === u.id && daysLeft(l.due) < 0).length;
      const tr = el('tr'); tr.append(el('td', 'mono', u.id), el('td', null, u.name), el('td', null, u.dept), el('td', 'num', `${n} / ${db.settings.maxBooks}`));
      const td = el('td'); td.append(over ? badge('overdue') : n ? badge('out') : badge('available')); tr.append(td); tb.append(tr);
    });
  }

  function renderSettings() {
    $('#set-days').value = db.settings.loanDays; $('#set-max').value = db.settings.maxBooks;
    $('#set-fail').classList.toggle('on', !!db.settings.failSim); $('#set-fail').setAttribute('aria-checked', String(!!db.settings.failSim));
    $('#version').textContent = 'schema v' + SCHEMA + ' ・ 保存先 localStorage["' + KEY + '"]';
  }

  // ── 貸出モーダル ──
  const modal = $('#lend-modal'); let lendTarget = null;
  function openLend(book) {
    lendTarget = book; $('#lend-title').textContent = `「${book.title}」を貸し出す`;
    const sel = $('#lend-user'); sel.replaceChildren(); db.users.forEach((u) => { const o = el('option', null, `${u.name}（${u.dept}）`); o.value = u.id; sel.append(o); });
    $('#lend-due').value = fmt(addDays(today(), db.settings.loanDays)); $('#lend-err').hidden = true; $('#lend-user').classList.remove('err');
    modal.classList.add('open'); sel.focus();
  }
  function closeLend() { modal.classList.remove('open'); lendTarget = null; }
  modal.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('.modal-close')) closeLend(); });
  $('#lend-submit').addEventListener('click', async () => {
    const userId = $('#lend-user').value; const held = db.loans.filter((l) => l.userId === userId).length;
    if (held >= db.settings.maxBooks) { $('#lend-user').classList.add('err'); $('#lend-err').hidden = false; $('#lend-err').lastChild.textContent = `この利用者は上限（${db.settings.maxBooks} 冊）まで借りています`; return; }
    const btn = $('#lend-submit'); btn.setAttribute('aria-busy', 'true'); const label = btn.textContent; btn.textContent = '処理中…';
    const done = window.Feedback.busy('貸出を記録しています');
    try {
      await tryWrite(() => { db.loans.push({ id: 'L-' + (1000 + db.loans.length + db.returned.length + 1), bookId: lendTarget.id, userId, out: fmt(today()), due: $('#lend-due').value }); save(db); });
      done({ ok: `「${lendTarget.title}」を貸し出しました` }); closeLend(); render();
    } catch (err) {
      done({ error: '貸出を記録できませんでした', detail: err.message, action: { label: 'もう一度貸し出す', onClick: () => $('#lend-submit').click() } });
    } finally { btn.removeAttribute('aria-busy'); btn.textContent = label; }
  });

  // ── 返却（確認 → 記録） ──
  async function doReturn(loan) {
    if (!loan) return;
    const b = bookOf(loan.bookId); const u = userOf(loan.userId);
    const yes = await window.Feedback.confirm({ title: `「${b.title}」を返却済みにする`, consequence: `${u.name} さんの貸出記録（${loan.id}）を返却済みにします。延滞日数は履歴に残ります。`, actionLabel: '返却済みにする' });
    if (!yes) { window.Feedback.info('返却をやめました'); return; }
    const done = window.Feedback.busy('返却を記録しています');
    try {
      await tryWrite(() => { db.loans = db.loans.filter((l) => l.id !== loan.id); db.returned.push({ ...loan, back: fmt(today()) }); save(db); });
      done({ ok: `「${b.title}」を返却済みにしました` }); render();
    } catch (err) {
      done({ error: '返却を記録できませんでした', detail: err.message, action: { label: 'もう一度返却する', onClick: () => doReturn(loan) } });
    }
  }

  // ── イベント配線 ──
  $('#book-q').addEventListener('input', (e) => { bookQuery = e.target.value; page = 1; renderBooks(); });
  $$('#book-seg span').forEach((s) => s.addEventListener('click', () => { setSeg(s.dataset.v); page = 1; renderBooks(); }));
  $('#set-save').addEventListener('click', async () => {
    const days = Number($('#set-days').value), max = Number($('#set-max').value);
    if (!(days >= 1 && days <= 60) || !(max >= 1 && max <= 10)) { window.Feedback.error('設定を保存できませんでした', { detail: '貸出期間は 1〜60 日、冊数上限は 1〜10 冊で入力してください。' }); return; }
    const done = window.Feedback.busy('設定を保存しています');
    try { await tryWrite(() => { db.settings.loanDays = days; db.settings.maxBooks = max; save(db); }); done({ ok: '設定を保存しました' }); render(); }
    catch (err) { done({ error: '設定を保存できませんでした', detail: err.message, action: { label: 'もう一度保存する', onClick: () => $('#set-save').click() } }); }
  });
  $('#set-fail').addEventListener('click', () => { db.settings.failSim = !db.settings.failSim; save(db); renderSettings(); window.Feedback.info(db.settings.failSim ? '以後、保存系の操作が失敗します（確認用）' : '通常に戻しました'); });
  $('#set-reset').addEventListener('click', async () => {
    const yes = await window.Feedback.confirm({ title: 'サンプルデータに戻す', consequence: 'いま入力した貸出・返却・設定はすべて消え、元に戻せません。', actionLabel: 'サンプルに戻す', danger: true });
    if (!yes) return; db = seed(); save(db); render(); window.Feedback.ok('サンプルデータに戻しました');
  });
  $('#set-import').addEventListener('click', () => window.Feedback.info('Excel の取り込みはモックでは動きません（本実装で対応）'));
  $('#export-csv').addEventListener('click', () => {
    const lines = [['貸出ID', '書名', '利用者', '貸出日', '返却期限', '状態'].join(',')].concat(db.loans.map((l) => [l.id, bookOf(l.bookId).title, userOf(l.userId).name, l.out, l.due, status(l.bookId) === 'overdue' ? '延滞' : '貸出中'].map((v) => '"' + String(v).replace(/"/g, '""') + '"').join(',')));
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv' }); const a = el('a'); a.href = URL.createObjectURL(blob); a.download = 'loans-' + fmt(today()) + '.csv'; a.click(); URL.revokeObjectURL(a.href);
    window.Feedback.ok('貸出中の一覧を CSV に書き出しました');
  });
  $('#theme').addEventListener('click', () => { const r = document.documentElement; r.dataset.theme = r.dataset.theme === 'dark' ? 'light' : 'dark'; });
  $('#menu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
  $('#collapse').addEventListener('click', () => $('#sidebar').classList.toggle('collapsed'));
  window.addEventListener('hashchange', route);
  route();
})();
