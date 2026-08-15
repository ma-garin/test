import * as fs from "node:fs";
import * as path from "node:path";
import { test as setup } from "@playwright/test";
import { ROLE_DEFS, AUTH_DIR, resolveCreds } from "./lib/roles";

/**
 * ロールごとにログインして storageState を保存する。
 *
 * - 資格情報が無い / ログインに失敗したロールは、空の state を書き
 *   マーカーファイル（<key>.ok）を作らない → 当該ロールのテストは skip される。
 * - 初回ログインのオンボーディング（テナント/案件選択）は先頭の選択肢で通過する。
 */

const EMPTY_STATE = JSON.stringify({ cookies: [], origins: [] });

async function completeSelection(page: import("@playwright/test").Page): Promise<void> {
  for (let i = 0; i < 4; i++) {
    const url = page.url();
    if (!/\/accounts\/(welcome|tenant|project)\//.test(url)) return;
    try {
      const radio = page.locator('form input[type="radio"]').first();
      if (await radio.count()) await radio.check();
      const select = page.locator("form select").first();
      if (await select.count()) await select.selectOption({ index: 1 }).catch(() => {});
      const submit = page.locator('form button[type="submit"], form input[type="submit"]').first();
      if (await submit.count()) {
        await submit.click();
        await page.waitForLoadState();
        continue;
      }
      // フォームが無ければ先頭のカード/リンクを選ぶ
      await page.locator("main a").first().click();
      await page.waitForLoadState();
    } catch {
      return;
    }
  }
}

for (const role of ROLE_DEFS) {
  setup(`authenticate: ${role.label} (${role.key})`, async ({ page }, testInfo) => {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    const statePath = path.join(AUTH_DIR, `${role.key}.json`);
    const okPath = path.join(AUTH_DIR, `${role.key}.ok`);
    if (fs.existsSync(okPath)) fs.unlinkSync(okPath);

    const creds = resolveCreds(role);
    if (!creds) {
      fs.writeFileSync(statePath, EMPTY_STATE);
      testInfo.annotations.push({
        type: "skip-role",
        description: `${role.userEnv} / ${role.passEnv} が未設定のため、このロールのテストは skip されます`,
      });
      return;
    }

    // パスワードレス認証: メールアドレスのみでログインする（apps/accounts/views.py）
    await page.goto("/accounts/login/");
    await page.locator('input[type="email"], input[name="email"]').first().fill(creds.user);
    await page.locator('form button[type="submit"], form input[type="submit"]').first().click();
    await page.waitForLoadState();

    if (page.url().includes("/accounts/login/")) {
      fs.writeFileSync(statePath, EMPTY_STATE);
      testInfo.annotations.push({
        type: "skip-role",
        description: `${role.label} のログインに失敗したため、このロールのテストは skip されます`,
      });
      return;
    }

    await completeSelection(page);
    await page.context().storageState({ path: statePath });
    fs.writeFileSync(okPath, "ok");
  });
}
