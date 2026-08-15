import * as fs from "node:fs";

/** フェーズ1 CSV の1行。列順は docs/system_test_cases_phase1.csv に従う。 */
export interface CaseRow {
  id: string;       // テストID
  category: string; // 大分類
  screen: string;   // 画面
  kind: string;     // 分類（正常系/異常系/境界値/権限/UI・UX/性能）
  role: string;     // ロール
  persona: string;  // ペルソナ
  purpose: string;  // テスト目的
  steps: string;    // 手順
  expected: string; // 期待される結果
}

/** ダブルクォート・カンマ入りセルに対応した最小CSVパーサ（依存なし）。 */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let field = "";
  let row: string[] = [];
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

export function loadCases(csvPath: string): CaseRow[] {
  const text = fs.readFileSync(csvPath, "utf8").replace(/^﻿/, "");
  const rows = parseCsv(text);
  const [header, ...body] = rows;
  if (!header || header[0] !== "テストID") {
    throw new Error(`CSVのヘッダーが想定と異なります: ${csvPath}`);
  }
  return body
    .filter((r) => r.length >= 9 && r[0].startsWith("TC-"))
    .map((r) => ({
      id: r[0],
      category: r[1],
      screen: r[2],
      kind: r[3],
      role: r[4],
      persona: r[5],
      purpose: r[6],
      steps: r[7],
      expected: r[8],
    }));
}
