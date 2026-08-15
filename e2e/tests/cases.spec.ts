import * as path from "node:path";
import { test, expect } from "@playwright/test";
import { loadCases, type CaseRow } from "../lib/csv";
import { SCREENS, DUMMY_UUID } from "../lib/screens";
import { ROLE_TAG, isRoleAvailable } from "../lib/roles";
import * as checks from "../lib/checks";

/**
 * フェーズ1のCSV（762件）を全件テストとして登録するデータ駆動スペック。
 *
 * - 自動判定できるパターン（下の planFor 参照）はアサーションで検証する
 * - 表示到達までは自動・最終判断が目視のものは「要目視確認」注釈付きで証跡を残す
 * - データ準備・既存IDが必要なものは理由付きで skip し、JSONレポートに全件残す
 *
 * どのテストがどう扱われたかは e2e/results/results.json で追跡できる。
 */

const CSV_PATH = path.resolve(__dirname, "../../docs/system_test_cases_phase1.csv");
const cases = loadCases(CSV_PATH);

type Plan =
  | { skip: string }
  | { run: (ctx: checks.CheckCtx, row: CaseRow) => Promise<void> };

/** 手順が「開いて確認する」だけか（データ操作を伴わないか） */
function isViewOnly(steps: string): boolean {
  return !/(登録|保存|入力し|実行する|切り替える|送信|削除|承認|投入|修正|任命|失効)/.test(steps);
}

function skipReason(row: CaseRow, needsId: boolean): string {
  if (needsId) return "既存データのIDが必要（シード整備後に対応）";
  if (/テナントA|テナントB|複数テナント/.test(row.steps)) return "複数テナントのシードデータが必要";
  if (/0件|空状態|データが少ない|古い状態|24時間以上|1,000件|100件規模/.test(row.steps + row.purpose))
    return "特定のデータ状態の準備が必要";
  if (!isViewOnly(row.steps)) return "データ操作を伴うため手動実行（将来フィクスチャ化）";
  return "手動確認（自動判定不可）";
}

function planFor(row: CaseRow): Plan {
  const screen = SCREENS[row.screen];

  // --- 画面固有: ログイン
  if (row.screen === "ログイン") {
    return { run: (ctx, r) => checks.loginCheck(ctx, r) };
  }

  // --- 横断
  if (row.category === "横断") {
    if (row.screen.includes("レスポンシブ")) return { run: (ctx, r) => checks.responsive(ctx, r) };
    if (row.screen.includes("エラーページ") && row.steps.includes("存在しないURL"))
      return { run: (ctx) => checks.custom404(ctx) };
    return { skip: skipReason(row, false) };
  }

  // --- 権限: 未認証リダイレクト
  if (row.kind === "権限" && (row.role === "（未認証）" || row.steps.includes("未ログイン"))) {
    const target = screen?.path ?? screen?.actionPath;
    if (target) return { run: (ctx) => checks.unauthRedirect(ctx, target) };
    return { skip: "該当画面のURL未登録（手動確認）" };
  }

  // --- 権限: 参照のみロールからのフォーム送信拒否
  if (row.kind === "権限" && row.role === "参照のみ" && row.purpose.includes("送信が拒否")) {
    if (screen?.formPath) return { run: (ctx) => checks.viewerFormRejected(ctx, screen.formPath!) };
    return { skip: skipReason(row, true) };
  }

  // --- 権限: CSRF
  if (row.kind === "権限" && row.steps.includes("CSRF")) {
    if (screen?.formPath) return { run: (ctx) => checks.csrfRejected(ctx, screen.formPath!) };
    return { skip: skipReason(row, true) };
  }

  // --- 異常系: 不正なクエリパラメータ
  if (row.kind === "異常系" && row.steps.includes("不正値")) {
    if (screen?.path && !screen.needsId) return { run: (ctx) => checks.badParams(ctx, screen.path!) };
    return { skip: skipReason(row, true) };
  }

  // --- 異常系: 実行系URLへのGET（IDが不要なもののみ）
  if (row.kind === "異常系" && row.steps.includes("GET") && screen?.actionPath) {
    return { run: (ctx) => checks.getNotAllowed(ctx, screen.actionPath!) };
  }

  if (!screen || (!screen.path && !screen.actionPath)) {
    return { skip: "該当画面のURL未登録（手動確認）" };
  }
  if (screen.needsId) return { skip: skipReason(row, true) };
  if (!screen.path) return { skip: skipReason(row, false) };

  // --- 正常系: 画面表示（役割理解）
  if (row.purpose.includes("画面の役割")) {
    return { run: (ctx) => checks.pageLoads(ctx, screen.path!) };
  }

  // --- 表示到達＋証跡（最終判断は目視） — 手順がデータ操作を伴わないものに限る
  if (isViewOnly(row.steps)) {
    return { run: (ctx, r) => checks.evidence(ctx, screen.path!, r.expected) };
  }

  return { skip: skipReason(row, false) };
}

// ---------------------------------------------------------------- テスト登録

for (const row of cases) {
  const tag = ROLE_TAG[row.role] ?? "pmo";
  const plan = planFor(row);
  const title = `${row.id} ${row.screen}: ${row.purpose} @role:${tag}`;

  test(title, async ({ page, browser }, testInfo) => {
    testInfo.annotations.push(
      { type: "大分類", description: row.category },
      { type: "分類", description: row.kind },
      { type: "ペルソナ", description: row.persona },
      { type: "期待される結果", description: row.expected },
    );

    if ("skip" in plan) test.skip(true, plan.skip);
    if (tag !== "anonymous") {
      test.skip(!isRoleAvailable(tag), `ロール「${row.role}」の資格情報未設定（e2e/README.md 参照）`);
    }

    await (plan as { run: (ctx: checks.CheckCtx, row: CaseRow) => Promise<void> }).run(
      { page, browser, testInfo },
      row,
    );
  });
}

// CSVが読めていること自体の健全性チェック（どの project でも1件は走るよう全ロール共通タグ無し・setup同等）
test.describe("csv sanity @role:anonymous", () => {
  test("CSVから500件以上のケースが読み込まれている @role:anonymous", async () => {
    expect(cases.length).toBeGreaterThanOrEqual(500);
    expect(new Set(cases.map((c) => c.id)).size).toBe(cases.length);
    void DUMMY_UUID;
  });
});
