import { expect, type Browser, type Page, type TestInfo } from "@playwright/test";
import type { CaseRow } from "./csv";
import { RESPONSIVE_PATHS } from "./screens";
import { ROLE_DEFS, resolveCreds } from "./roles";

/** 検証用の共通引数 */
export interface CheckCtx {
  page: Page;
  browser: Browser;
  testInfo: TestInfo;
}

export async function attachShot(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  const body = await page.screenshot({ fullPage: true });
  await testInfo.attach(name, { body, contentType: "image/png" });
}

/** 画面が表示できること（ログインへ飛ばされていないこと）を確認し、証跡を残す。 */
export async function pageLoads(ctx: CheckCtx, path: string): Promise<void> {
  const resp = await ctx.page.goto(path);
  expect(resp, "レスポンスが取得できること").toBeTruthy();
  expect(resp!.status(), "4xx/5xxを返さないこと").toBeLessThan(400);
  expect(ctx.page.url(), "ログインへリダイレクトされないこと").not.toContain("/accounts/login/");
  // サイドバー内の折りたたまれた h2 が DOM 順で先に来るため、可視要素に限定する。
  // base.html は main+h1#page-heading、auth.html は h2.auth-title を持つ。
  await expect(
    ctx.page.locator("h1:visible, h2:visible, main:visible").first(),
    "見出しまたは本文領域が表示されること",
  ).toBeVisible();
  await attachShot(ctx.page, ctx.testInfo, "evidence");
}

/** 表示到達＋証跡取得。期待結果の最終判断は添付画像・動画で人が行う。 */
export async function evidence(ctx: CheckCtx, path: string, expected: string): Promise<void> {
  ctx.testInfo.annotations.push({ type: "要目視確認", description: expected });
  await pageLoads(ctx, path);
}

/** 未ログインでのアクセスがログインへ誘導されること。 */
export async function unauthRedirect(ctx: CheckCtx, path: string): Promise<void> {
  await ctx.page.goto(path);
  expect(ctx.page.url(), "ログイン画面へ誘導されること").toContain("/accounts/login/");
  await attachShot(ctx.page, ctx.testInfo, "login-redirect");
}

/** 不正なクエリパラメータで500にならないこと。 */
export async function badParams(ctx: CheckCtx, path: string): Promise<void> {
  const sep = path.includes("?") ? "&" : "?";
  const resp = await ctx.page.goto(`${path}${sep}page=abc&per_page=-1&q=%2500zz&p=99999999`);
  expect(resp, "レスポンスが取得できること").toBeTruthy();
  expect(resp!.status(), "500を返さないこと").toBeLessThan(500);
  await attachShot(ctx.page, ctx.testInfo, "bad-params");
}

/** CSRFトークン無しのPOSTが403で拒否されること。 */
export async function csrfRejected(ctx: CheckCtx, formPath: string): Promise<void> {
  const resp = await ctx.page.request.post(formPath, {
    form: { _probe: "csrf" },
    maxRedirects: 0,
    failOnStatusCode: false,
  });
  expect(resp.status(), "CSRF無しPOSTは403であること").toBe(403);
}

/** 参照のみロールからのフォーム送信が権限で拒否されること。
 * 実装によりフォーム表示自体を403にする画面と、表示は許しPOSTで弾く画面の
 * 両方があり得るため、GETが403ならそこで合格、200ならCSRF付きPOSTの403を確認する。 */
export async function viewerFormRejected(ctx: CheckCtx, formPath: string): Promise<void> {
  const resp = await ctx.page.goto(formPath);
  if (resp && resp.status() === 403) {
    await attachShot(ctx.page, ctx.testInfo, "rejected-403");
    return;
  }
  const token = await ctx.page
    .locator('input[name="csrfmiddlewaretoken"]')
    .first()
    .inputValue()
    .catch(() => "");
  const post = await ctx.page.request.post(formPath, {
    form: { csrfmiddlewaretoken: token },
    maxRedirects: 0,
    failOnStatusCode: false,
  });
  // 案件系フォームは 403 ではなく「参照のみロールには案件の選択肢を出さない」設計で
  // 防いでいる（apps/projects/permissions.py editable_projects_for → projects.none()）。
  // 登録が成功すると一覧へ 3xx リダイレクトするため、3xx でなければ登録されていない。
  ctx.testInfo.annotations.push({
    type: "設計メモ",
    description: "拒否方式は2系統: 明示403（外部連携）／選択肢の空化＋再描画（案件系、要件#30）",
  });
  expect([301, 302, 303], "登録成功のリダイレクトが発生しないこと").not.toContain(post.status());
  expect(post.status(), "500にならないこと").toBeLessThan(500);
}

/** IDを取らない実行系URLへのGETが実行として扱われないこと。 */
export async function getNotAllowed(ctx: CheckCtx, actionPath: string): Promise<void> {
  const resp = await ctx.page.request.get(actionPath, {
    maxRedirects: 0,
    failOnStatusCode: false,
  });
  expect(
    [301, 302, 303, 403, 405],
    "GETは405または実行されずに終わること",
  ).toContain(resp.status());
}

/** 指定幅で横スクロールが発生しないこと。幅は手順文の「NNNpx」から拾う。 */
export async function responsive(ctx: CheckCtx, row: CaseRow): Promise<void> {
  const widths = [...row.steps.matchAll(/(\d{3,4})px/g)].map((m) => Number(m[1]));
  const targets = RESPONSIVE_PATHS.filter(([re]) => re.test(row.steps)).map(([, p]) => p);
  const useWidths = widths.length > 0 ? widths : [375, 768];
  const usePaths = targets.length > 0 ? targets : ["/"];

  for (const width of useWidths) {
    await ctx.page.setViewportSize({ width, height: 800 });
    for (const path of usePaths) {
      await ctx.page.goto(path);
      const overflow = await ctx.page.evaluate(() => {
        const el = document.scrollingElement!;
        return el.scrollWidth - el.clientWidth;
      });
      expect(overflow, `${path} を幅${width}pxで開いても横スクロールしないこと`).toBeLessThanOrEqual(2);
      await attachShot(ctx.page, ctx.testInfo, `w${width}-${path.replace(/\W+/g, "_")}`);
    }
  }
}

/** 存在しないURLがカスタム404になること。 */
export async function custom404(ctx: CheckCtx): Promise<void> {
  const resp = await ctx.page.goto("/definitely-not-a-page-xyz/");
  expect(resp!.status(), "404を返すこと").toBe(404);
  await expect(ctx.page.locator("body")).toContainText(/404|見つかりません|Not Found/i);
  await attachShot(ctx.page, ctx.testInfo, "custom-404");
}

// ---------------------------------------------------------------- ログイン画面固有

// 認証は apps/accounts/views.py login_view のパスワードレス方式（メールのみ）。
// 弾かれるのは形式不正と無効化済み利用者のみで、未知メールは利用者を自動作成する。
const EMAIL_INPUT = 'input[type="email"], input[name="email"]';
const SUBMIT_BUTTON = 'form button[type="submit"], form input[type="submit"]';
const ERROR_BOX = '.callout.d, .errorlist, [role="alert"], .alert';

async function fillLogin(page: Page, email: string): Promise<void> {
  await page.goto("/accounts/login/");
  await page.locator(EMAIL_INPUT).first().fill(email);
}

function pmoEmail(): string {
  const creds = resolveCreds(ROLE_DEFS.find((r) => r.key === "pmo")!);
  if (!creds) throw new Error("PMO担当の資格情報が未設定です");
  return creds.user;
}

/** ログイン画面のケースをテスト目的のキーワードで振り分けて検証する。 */
export async function loginCheck(ctx: CheckCtx, row: CaseRow): Promise<void> {
  // 常に未認証の文脈で検証する。browser.newContext() は project の storageState
  // （ログイン済み）を継承するため、空の state を明示して素のコンテキストにする。
  const context = await ctx.browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  const local: CheckCtx = { ...ctx, page };

  try {
    if (row.purpose.includes("正しい資格情報")) {
      ctx.testInfo.annotations.push({ type: "仕様", description: "パスワードレス認証のためメールアドレスのみでログインする" });
      await fillLogin(page, pmoEmail());
      await page.locator(SUBMIT_BUTTON).first().click();
      await page.waitForLoadState();
      expect(page.url(), "ログイン画面から遷移すること").not.toContain("/accounts/login/");
      await attachShot(page, ctx.testInfo, "after-login");
    } else if (row.purpose.includes("無効化済み") || row.purpose.includes("存在しない利用者ID")) {
      // 無効化済み利用者は authenticate が None を返し、汎用メッセージで弾かれる
      // （利用者は e2e/seed_role_users.py が投入する）
      const email = process.env.E2E_DEACTIVATED_EMAIL ?? "deactivated@example.com";
      await fillLogin(page, email);
      await page.locator(SUBMIT_BUTTON).first().click();
      await page.waitForLoadState();
      expect(page.url(), "ログイン画面に留まること").toContain("/accounts/login/");
      const box = page.locator(ERROR_BOX).first();
      await expect(box).toBeVisible();
      await expect(box, "汎用メッセージで拒否されること").toContainText("ログインできません");
      await attachShot(page, ctx.testInfo, "deactivated-login");
    } else if (row.purpose.includes("形式不正") || row.purpose.includes("誤ったパスワード")) {
      // パスワードが存在しないため、「認証失敗時のエラー表示と入力保持」という
      // 元の意図を形式不正メールで代替検証する。HTML5検証は noValidate で迂回し
      // サーバ側のエラー描画を確認する。
      ctx.testInfo.annotations.push({ type: "仕様差異", description: "形式不正メールで認証失敗時のエラー表示・入力保持を検証" });
      await page.goto("/accounts/login/");
      await page.evaluate(() => {
        const form = document.querySelector("form") as HTMLFormElement;
        const input = form.querySelector('input[type="email"], input[name="email"]') as HTMLInputElement;
        input.value = "invalid-format";
        form.noValidate = true;
        form.submit();
      });
      await page.waitForLoadState();
      expect(page.url(), "ログイン画面に留まること").toContain("/accounts/login/");
      await expect(page.locator(ERROR_BOX).first()).toBeVisible();
      await expect(page.locator(EMAIL_INPUT).first(), "入力値が保持されること").toHaveValue("invalid-format");
      await attachShot(page, ctx.testInfo, "login-error");
    } else if (row.purpose.includes("空欄・空白")) {
      await page.goto("/accounts/login/");
      const resp = await page.request.post("/accounts/login/", { form: {}, failOnStatusCode: false });
      expect(resp.status(), "500を返さないこと").toBeLessThan(500);
    } else if (row.purpose.includes("ログイン済みでログイン画面")) {
      // この行だけは project の storageState（ログイン済み）を使う
      await ctx.page.goto("/accounts/login/");
      expect(ctx.page.url(), "ログインフォームではなく適切な画面へ誘導されること").not.toMatch(/\/accounts\/login\/?$/);
      await attachShot(ctx.page, ctx.testInfo, "already-logged-in");
    } else if (row.purpose.includes("Enterキー") || row.purpose.includes("パスワード入力")) {
      // マスク観点はパスワードレス認証のため対象外。Enterキーでの送信性のみ検証する
      ctx.testInfo.annotations.push({ type: "仕様差異", description: "パスワード欄が無いため、Enter送信の使い勝手のみ検証（マスク観点はCSV改訂候補）" });
      await fillLogin(page, pmoEmail());
      await page.locator(EMAIL_INPUT).first().press("Enter");
      await page.waitForLoadState();
      expect(page.url(), "Enterキーで送信できること").not.toContain("/accounts/login/");
    } else if (row.purpose.includes("リダイレクト付き")) {
      await page.goto("/core/settings/");
      expect(page.url(), "ログインへ誘導されること").toContain("/accounts/login/");
      await page.locator(EMAIL_INPUT).first().fill(pmoEmail());
      await page.locator(SUBMIT_BUTTON).first().click();
      await page.waitForLoadState();
      expect(page.url(), "最初に開こうとした画面へ戻ること").toContain("/core/settings/");
    } else {
      ctx.testInfo.skip(true, "手動確認（画面に『回数制限はありません』と明記。防御方針の要否は docs/e2e_odc_phase3.md 参照）");
    }
  } finally {
    await context.close();
  }
}
