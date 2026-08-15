import * as fs from "node:fs";
import * as path from "node:path";

/**
 * ロール定義。apps/accounts/constants.py の Role と1対1。
 * 認証はパスワードレス（メールアドレスのみ）。userEnv にはメールアドレスを渡す。
 * 未設定のロールのテストは skip される。PMO担当のみデモ利用者（pmo@example.com）が既定値。
 */
export interface RoleDef {
  key: string;
  label: string;
  userEnv: string;
  passEnv: string;
  defaultUser?: string;
  defaultPass?: string;
}

export const AUTH_DIR = "e2e/.auth";

export const ROLE_DEFS: RoleDef[] = [
  { key: "pmo", label: "PMO担当", userEnv: "E2E_USER_PMO", passEnv: "E2E_PASS_PMO", defaultUser: "pmo@example.com" },
  // 既定メールは e2e/seed_role_users.py が投入する利用者。未投入のDBでは
  // 自動作成（PMO担当扱い）になり権限テストが成立しないため、先に投入すること。
  { key: "pmpl", label: "PM・PL", userEnv: "E2E_USER_PMPL", passEnv: "E2E_PASS_PMPL", defaultUser: "pm@example.com" },
  { key: "quality", label: "品質責任者", userEnv: "E2E_USER_QUALITY", passEnv: "E2E_PASS_QUALITY", defaultUser: "quality@example.com" },
  { key: "change", label: "変更管理者", userEnv: "E2E_USER_CHANGE", passEnv: "E2E_PASS_CHANGE", defaultUser: "change@example.com" },
  { key: "viewer", label: "参照のみ", userEnv: "E2E_USER_VIEWER", passEnv: "E2E_PASS_VIEWER", defaultUser: "viewer@example.com" },
  { key: "tenantadmin", label: "テナント管理者", userEnv: "E2E_USER_TENANT_ADMIN", passEnv: "E2E_PASS_TENANT_ADMIN", defaultUser: "tenantadmin@example.com" },
  { key: "sysadmin", label: "システム管理者", userEnv: "E2E_USER_SYSADMIN", passEnv: "E2E_PASS_SYSADMIN", defaultUser: "sysadmin@example.com" },
];

/** CSVの「ロール」列 → project タグ */
export const ROLE_TAG: Record<string, string> = {
  "PMO担当": "pmo",
  "PM・PL": "pmpl",
  "品質責任者": "quality",
  "変更管理者": "change",
  "参照のみ": "viewer",
  "テナント管理者": "tenantadmin",
  "システム管理者": "sysadmin",
  "（未認証）": "anonymous",
};

export function resolveCreds(role: RoleDef): { user: string; pass: string } | null {
  const user = process.env[role.userEnv] ?? role.defaultUser;
  if (!user) return null;
  // 現行実装はパスワードレス認証のため pass は未使用（将来の方式変更に備えて保持）
  const pass = process.env[role.passEnv] ?? role.defaultPass ?? "";
  return { user, pass };
}

/** setup が資格情報ありでログイン成功したロールか（マーカーファイル方式） */
export function isRoleAvailable(tag: string): boolean {
  if (tag === "anonymous") return true;
  return fs.existsSync(path.join(AUTH_DIR, `${tag}.ok`));
}
