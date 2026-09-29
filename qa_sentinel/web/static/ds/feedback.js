/*
 * feedback.js — 操作に対する手応えを返す部品（自己完結。CSS を自分で注入する）
 *
 * 押しても何も変わらないと、利用者は完了したのか失敗したのか判断できない。
 * 待つべきなのか、もう一度押すべきなのかも分からない。
 * だから「操作したら必ず何か返す」を製品全体の約束にし、出し方をこの 1 モジュールに揃える。
 *
 *   Feedback.ok('保存しました')                                   3 秒ほどで消える
 *   Feedback.error('保存できませんでした', { detail, action })       消えない。次の一手を必ず添える
 *   Feedback.info('3 件を読み込みました')
 *   const done = Feedback.busy('読み込んでいます'); done({ ok: '読み込みました' })
 *   el.replaceChildren(Feedback.emptyState({ title, description, icon, action }))
 *   const yes = await Feedback.confirm({ title, consequence, actionLabel, danger: true })
 *
 * 前提: templates/tokens.css を先に読み込む（--color-* / --space-* / --radius-* / --shadow-pop / --motion-*）。
 *       icons.js があればそのアイコンを使い、無ければ内蔵の線画 SVG に切り替わる。
 * 文字は textContent で入れる。ここに渡る文言にはサーバから返ったエラー文が混ざるため HTML として解釈させない。
 * アイコンだけは自前の SVG なので innerHTML で入れてよい。
 *
 * 出所: UX_Auto_Reviewer web/components/feedback.js（2026-08）を tokens.css 前提に汎用化。
 * 規約: skills/design-system/SKILL.md「操作には必ず結果を返す」
 */
'use strict';

(function () {
  const AUTO_HIDE_MS = { ok: 3200, info: 4000, error: 0 };  // 失敗は自分で閉じるまで消さない
  const LEAVE_MS = 200;
  const ICON = { ok: 'check', error: 'circle-alert', info: 'info', busy: 'loader-circle', close: 'x' };

  // icons.js が無いときの最小セット（Lucide 風の線画。ISC ライセンス相当の単純図形）
  const FALLBACK = {
    'check': '<polyline points="20 6 9 17 4 12"/>',
    'circle-alert': '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
    'info': '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    'loader-circle': '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    'x': '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'inbox': '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
  };
  function iconSvg(name, size) {
    if (window.Icons && window.Icons.names && window.Icons.names.includes(name)) return window.Icons.svg(name, size);
    const body = FALLBACK[name] || FALLBACK.info;
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="' + size + '" height="' + size + '"'
      + ' fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
      + ' class="icon icon-' + name + '" aria-hidden="true" focusable="false">' + body + '</svg>';
  }

  const CSS = `
.toast-host { position: fixed; top: 80px; left: 50%; transform: translateX(-50%); z-index: var(--z-toast);
  display: flex; flex-direction: column; align-items: stretch; gap: var(--space-2, 8px);
  width: min(360px, calc(100vw - var(--space-8, 32px))); pointer-events: none; }
.toast { display: flex; align-items: flex-start; gap: var(--space-3, 12px); padding: var(--space-3, 12px) var(--space-4, 16px);
  border: 1px solid var(--color-border, #E0E0E0); border-radius: var(--radius-md, 8px); background: var(--color-surface, #fff);
  color: var(--color-text, #212121); box-shadow: var(--shadow-pop, 0 12px 32px rgba(20,32,50,.14)); pointer-events: auto;
  font-size: var(--text-base, 14px); line-height: var(--leading-normal, 1.7);
  opacity: 0; transform: translateY(6px); transition: opacity var(--motion-normal, .2s), transform var(--motion-normal, .2s); }
.toast.is-shown { opacity: 1; transform: none; }
.toast.is-leaving { opacity: 0; transform: translateY(4px); }
.toast-icon { display: flex; margin-top: 3px; flex: none; }
.toast-ok    { border-color: var(--color-low-border, #A5D6A7); }    .toast-ok .toast-icon    { color: var(--color-low, #317C34); }
.toast-error { border-color: var(--color-critical-border, #F4B4B4); } .toast-error .toast-icon { color: var(--color-critical, #CD2B2B); }
.toast-info  .toast-icon { color: var(--color-info, #0272B0); }
.toast-busy  .toast-icon { color: var(--color-text-secondary, #616161); }
.toast-icon-spin .icon { animation: toast-spin var(--motion-spin) linear infinite; }
@keyframes toast-spin { to { transform: rotate(360deg); } }
.toast-body { flex: 1; min-width: 0; }
.toast-message { margin: 0; font-weight: 600; }
.toast-detail { margin: var(--space-1, 4px) 0 0; color: var(--color-text-secondary, #616161); font-size: var(--text-sm, 13px); }
.toast-action { display: inline-block; margin-top: var(--space-2, 8px); padding: 0; border: 0; background: none;
  color: var(--color-primary, #176DC2); font: inherit; font-weight: 600; cursor: pointer; text-decoration: underline; min-height: 0; min-width: 0; }
.toast-close { flex: none; border: 0; background: none; color: var(--color-text-secondary, #616161); cursor: pointer;
  padding: 2px; min-height: 0; min-width: 0; display: flex; border-radius: var(--radius-sm, 4px); }
.toast-close:hover { background: var(--color-surface-2, #F1F3F4); }
.empty-state { text-align: center; padding: var(--space-12, 48px) var(--space-4, 16px); color: var(--color-text-secondary, #616161);
  display: flex; flex-direction: column; align-items: center; gap: var(--space-4, 16px); }
.empty-state-icon { color: var(--color-text-disabled, #9E9E9E); }
.empty-state-title { margin: 0; color: var(--color-text, #212121); font-weight: 600; font-size: var(--text-md, 16px); }
.empty-state-description { margin: 0; font-size: var(--text-sm, 13px); max-width: var(--text-measure, 68ch); }
.empty-state .btn-primary { background: var(--color-primary, #176DC2); color: var(--color-on-primary, #fff); border: 0;
  border-radius: var(--radius-md, 8px); padding: 0 var(--space-4, 16px); font: inherit; font-weight: 600; cursor: pointer; text-decoration: none;
  display: inline-flex; align-items: center; justify-content: center; }
.confirm-dialog { border: 1px solid var(--color-border, #E0E0E0); border-radius: var(--radius-lg, 12px); padding: var(--space-6, 24px);
  background: var(--color-surface, #fff); color: var(--color-text, #212121); box-shadow: var(--shadow-pop, 0 12px 32px rgba(20,32,50,.14));
  width: min(440px, calc(100vw - var(--space-8, 32px))); font-family: inherit; }
.confirm-dialog::backdrop { background: var(--color-scrim, rgba(0,0,0,.4)); }
.confirm-title { margin: 0 0 var(--space-2, 8px); font-size: var(--text-lg, 18px); font-weight: 700; }
.confirm-consequence { margin: 0 0 var(--space-6, 24px); color: var(--color-text-secondary, #616161); }
.confirm-actions { display: flex; justify-content: flex-end; gap: var(--space-2, 8px); }
.confirm-actions button { border-radius: var(--radius-md, 8px); padding: 0 var(--space-4, 16px); font: inherit; font-weight: 600; cursor: pointer; }
.confirm-cancel { background: var(--color-surface, #fff); color: var(--color-text, #212121); border: 1px solid var(--color-border, #E0E0E0); }
.confirm-ok { background: var(--color-primary, #176DC2); color: var(--color-on-primary, #fff); border: 0; }
.confirm-ok.is-danger { background: var(--color-critical, #CD2B2B); }
@media (prefers-reduced-motion: reduce) { .toast { transition: none; } }
`;
  function ensureCss() {
    if (document.getElementById('feedback-css')) return;
    const style = document.createElement('style');
    style.id = 'feedback-css';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  let host = null;
  function ensureHost() {
    ensureCss();
    if (host && document.body.contains(host)) return host;
    host = document.createElement('div');
    host.className = 'toast-host';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
    return host;
  }

  function iconElement(name, className) {
    const span = document.createElement('span');
    span.className = className;
    span.innerHTML = iconSvg(name, 18);   // 自前の SVG。外から来た文字列は通らない
    return span;
  }
  function textNode(tag, className, text) {
    const el = document.createElement(tag);
    el.className = className;
    el.textContent = String(text == null ? '' : text);
    return el;
  }
  /** 遷移先として安全な URL だけを通す。javascript: が入ると押した瞬間に任意のコードが動く */
  function safeHref(href) {
    try {
      const url = new URL(String(href), window.location.origin);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }
  function actionElement(action, className, onDone) {
    if (!action || !action.label) return null;
    const href = action.href ? safeHref(action.href) : null;
    if (href) { const a = textNode('a', className, action.label); a.href = href; return a; }
    if (action.onClick) {
      const b = textNode('button', className, action.label);
      b.type = 'button';
      b.addEventListener('click', () => { if (onDone) onDone(); action.onClick(); });
      return b;
    }
    return null;
  }
  function removeLater(toast) {
    if (!toast.isConnected) return;
    toast.classList.add('is-leaving');
    setTimeout(() => toast.remove(), LEAVE_MS);   // 消える動きの分だけ待つ。すぐ消すと視線が追えない
  }

  /** 直前に押した場所を覚え、その近くに出す。画面の隅に固定すると押した結果と結び付かない */
  let lastPointer = null;
  document.addEventListener('pointerdown', (event) => {
    const el = event.target instanceof Element ? event.target.closest('button, a, [role="button"]') : null;
    lastPointer = el ? el.getBoundingClientRect() : null;
  }, true);
  function placeNearLastAction(h) {
    const rect = lastPointer;
    if (!rect) { h.removeAttribute('style'); return; }
    const width = Math.min(360, window.innerWidth - 32);
    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(16, Math.min(left, window.innerWidth - width - 16));
    let top = rect.bottom + 12;
    if (top + 80 > window.innerHeight) top = Math.max(16, rect.top - 80);
    h.style.top = Math.round(top) + 'px';
    h.style.left = Math.round(left) + 'px';
    h.style.width = width + 'px';
    h.style.transform = 'none';
  }

  /**
   * @param {'ok'|'error'|'info'} kind
   * @param {string} message 何が起きたか
   * @param {{detail?: string, action?: {label: string, href?: string, onClick?: Function}, autoHideMs?: number}} [options]
   * @returns {Function} 手で閉じる関数
   */
  function show(kind, message, options = {}) {
    const toast = document.createElement('div');
    toast.className = 'toast toast-' + kind;
    if (kind === 'error') toast.setAttribute('role', 'alert');   // 失敗は読み上げの割り込みを許す
    toast.appendChild(iconElement(ICON[kind] || ICON.info, 'toast-icon'));

    const body = document.createElement('div');
    body.className = 'toast-body';
    body.appendChild(textNode('p', 'toast-message', message));
    // 失敗のときは必ず次の一手を出す。空文字を渡せば足さない（null で判定する）
    const detail = options.detail != null
      ? options.detail
      : (kind === 'error' ? '時間をおいて、もう一度お試しください。続くときは管理者に連絡してください。' : '');
    if (detail) body.appendChild(textNode('p', 'toast-detail', detail));
    const close = () => removeLater(toast);
    const action = actionElement(options.action, 'toast-action', close);
    if (action) body.appendChild(action);
    toast.appendChild(body);

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'toast-close';
    closeButton.setAttribute('aria-label', '閉じる');
    closeButton.innerHTML = iconSvg(ICON.close, 16);
    closeButton.addEventListener('click', close);
    toast.appendChild(closeButton);

    const h = ensureHost();
    placeNearLastAction(h);
    h.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('is-shown'));
    const hideAfter = options.autoHideMs != null ? options.autoHideMs : AUTO_HIDE_MS[kind];
    if (hideAfter > 0) setTimeout(close, hideAfter);
    return close;
  }

  /** 処理中を出す。返った関数を呼ぶと消える。何も出さずに待たせると固まったのか判断できない */
  function busy(message) {
    const toast = document.createElement('div');
    toast.className = 'toast toast-busy';
    toast.appendChild(iconElement(ICON.busy, 'toast-icon toast-icon-spin'));
    const body = document.createElement('div');
    body.className = 'toast-body';
    body.appendChild(textNode('p', 'toast-message', message));
    toast.appendChild(body);
    const h = ensureHost();
    placeNearLastAction(h);
    h.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('is-shown'));
    return (result) => {
      removeLater(toast);
      if (result && result.ok) show('ok', result.ok);                 // 終わったことも知らせる
      else if (result && result.error) show('error', result.error, result);
    };
  }

  /** 0 件の表示。枠だけが残ると読み込み中なのか本当に 0 件なのか区別できない */
  function emptyState(options = {}) {
    ensureCss();
    const el = document.createElement('div');
    el.className = 'empty-state';
    el.appendChild(iconElement(options.icon || 'inbox', 'empty-state-icon'));
    el.appendChild(textNode('p', 'empty-state-title', options.title || 'まだありません'));
    if (options.description) el.appendChild(textNode('p', 'empty-state-description', options.description));
    const action = actionElement(options.action, 'btn-primary');
    if (action) el.appendChild(action);
    return el;
  }

  /**
   * 危険な操作の前の確認。何が起きるかを明記し、ボタンは動作名にする（「はい／いいえ」を使わない）
   * @returns {Promise<boolean>}
   */
  function confirm(options = {}) {
    ensureCss();
    return new Promise((resolve) => {
      const dialog = document.createElement('dialog');
      dialog.className = 'confirm-dialog';
      dialog.appendChild(textNode('h2', 'confirm-title', options.title || 'この操作を実行しますか'));
      if (options.consequence) dialog.appendChild(textNode('p', 'confirm-consequence', options.consequence));
      const actions = document.createElement('div');
      actions.className = 'confirm-actions';
      const cancel = textNode('button', 'confirm-cancel', options.cancelLabel || 'やめる');
      const ok = textNode('button', 'confirm-ok' + (options.danger ? ' is-danger' : ''), options.actionLabel || '実行する');
      cancel.type = 'button'; ok.type = 'button';
      const finish = (value) => { dialog.close(); dialog.remove(); resolve(value); };
      cancel.addEventListener('click', () => finish(false));
      ok.addEventListener('click', () => finish(true));
      dialog.addEventListener('cancel', (e) => { e.preventDefault(); finish(false); });
      actions.appendChild(cancel); actions.appendChild(ok);
      dialog.appendChild(actions);
      document.body.appendChild(dialog);
      if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
      cancel.focus();
    });
  }

  window.Feedback = {
    ok: (message, options) => show('ok', message, options),
    error: (message, options) => show('error', message, options),
    info: (message, options) => show('info', message, options),
    busy,
    emptyState,
    confirm
  };
})();
