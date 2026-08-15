import { defineConfig, devices } from "@playwright/test";
import { ROLE_DEFS, AUTH_DIR } from "./e2e/lib/roles";

/**
 * フェーズ2 E2E 設定。
 *
 * - ロール別に project を分け、fullyParallel で並列実行する
 * - 各テストのタイトル末尾の @role:<key> タグで project へ振り分ける
 * - スクリーンショット・動画は e2e/artifacts/ へ、結果JSONは e2e/results/ へ出す
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  retries: 0,
  outputDir: "e2e/artifacts",
  reporter: [
    ["list"],
    ["json", { outputFile: "e2e/results/results.json" }],
    ["html", { outputFolder: "e2e/results/html", open: "never" }],
  ],
  // Django をPlaywrightと同一のフォアグラウンドプロセス内で起動・終了する。
  // 既に起動済みならそれを使う（reuseExistingServer）。
  webServer: {
    command: ".venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload",
    url: "http://127.0.0.1:8000/healthz/",
    reuseExistingServer: true,
    timeout: 60_000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8000",
    screenshot: "on",
    video: "on",
    trace: "retain-on-failure",
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
  },
  projects: [
    {
      name: "setup",
      testMatch: /auth\.setup\.ts$/,
      fullyParallel: false,
      use: { ...devices["Desktop Chrome"] },
    },
    // 認証済みロールごとの project。storageState は setup が作る。
    ...ROLE_DEFS.map((role) => ({
      name: `role-${role.key}`,
      testMatch: /tests\/.*\.spec\.ts$/,
      grep: new RegExp(`@role:${role.key}\\b`),
      dependencies: ["setup"],
      use: {
        ...devices["Desktop Chrome"],
        storageState: `${AUTH_DIR}/${role.key}.json`,
      },
    })),
    {
      name: "anonymous",
      testMatch: /tests\/.*\.spec\.ts$/,
      grep: /@role:anonymous\b/,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
