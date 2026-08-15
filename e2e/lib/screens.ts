/**
 * CSVの「画面」列 → URL の対応表。
 * 出典: config/urls.py と各アプリの urls.py（2026-08-15 時点のコードが正）。
 *
 * - path     : ログイン後にそのまま開けるURL
 * - needsId  : 既存データのUUIDが必要（自動テストでは未解決 → 未認証確認のみ可能）
 * - formPath : POST先（CSRF検証に使う）
 * - actionPath: 状態変更操作のURL（IDが不要なもののみ）
 */
export interface ScreenDef {
  path?: string;
  needsId?: boolean;
  formPath?: string;
  actionPath?: string;
}

/** 存在しないUUID。未認証リダイレクトや404確認に使う。 */
export const DUMMY_UUID = "00000000-0000-0000-0000-000000000000";

export const SCREENS: Record<string, ScreenDef> = {
  // 認証・切替
  "ログイン": { path: "/accounts/login/" },
  "ログアウト": { actionPath: "/accounts/logout/" },
  "テナント切替": { path: "/accounts/tenant/" },
  "案件切替": { path: "/accounts/project/" },
  "初回テナント選択": { path: "/accounts/welcome/tenant/" },
  "初回案件選択": { path: "/accounts/welcome/project/" },
  // 進捗・着地
  "プロジェクトダッシュボード": { path: "/" },
  "タスク一覧": { path: "/tasks/" },
  "進捗予測・介入": { path: "/progress/" },
  "ライブ着地予測": { path: "/forecast/" },
  "日次・週次報告": { path: "/forecast/report/" },
  "タスク詳細": { path: `/projects/tasks/${DUMMY_UUID}/`, needsId: true },
  "タスク新規作成": { path: "/projects/tasks/new/", formPath: "/projects/tasks/new/" },
  "タスク編集": { path: `/projects/tasks/${DUMMY_UUID}/edit/`, needsId: true },
  "タスクのアーカイブ": { needsId: true },
  // 品質・リスク
  "品質リアルタイム管理": { path: "/quality/" },
  "不具合管理": { path: "/projects/defects/" },
  "不具合新規登録": { path: "/projects/defects/new/", formPath: "/projects/defects/new/" },
  "不具合編集": { needsId: true },
  "不具合のクローズ": { needsId: true },
  "課題管理": { path: "/projects/issues/" },
  "課題新規作成": { path: "/projects/issues/new/", formPath: "/projects/issues/new/" },
  "課題編集": { needsId: true },
  "課題のクローズ": { needsId: true },
  "リスク予測・対策": { path: "/risk/" },
  "リスク新規作成": { path: "/projects/risks/new/", formPath: "/projects/risks/new/" },
  "リスク編集": { needsId: true },
  "リスクのクローズ": { needsId: true },
  "リスクを課題へ転換": { needsId: true },
  "変更影響分析": { path: "/change/" },
  "変更要求の新規作成": { path: "/projects/changes/new/", formPath: "/projects/changes/new/" },
  "変更要求編集": { needsId: true },
  "変更要求の判断": { needsId: true },
  "予兆検知": { path: "/detection/" },
  "検知の実行": { actionPath: "/detection/run/" },
  "AI介入提案": { path: "/intervention/" },
  "AI介入提案の判断": { needsId: true },
  // 評価・データ品質
  "KPI・効果測定": { path: "/kpi/" },
  "PoC合否判定": { path: "/poc/" },
  "グラフ品質・データ整備": { path: "/graph/quality/" },
  // PMO支援
  "PMO相談・状況整理": { path: "/pmo/consultation/" },
  "計画ドラフト": { path: "/pmo/planning/" },
  "成果物支援": { path: "/pmo/deliverables/" },
  "報告生成・承認": { path: "/pmo/approvals/" },
  "プロンプトライブラリ": { path: "/pmo/prompts/" },
  "教育支援": { path: "/pmo/education/" },
  // ナレッジ / RAG
  "ナレッジ一覧": { path: "/documents/" },
  "ナレッジ登録": { path: "/documents/upload/", formPath: "/documents/upload/" },
  "ひな型一覧": { path: "/documents/templates/" },
  "RAG検索": { path: "/rag/search/" },
  "チャットモード": { path: "/rag/chat/" },
  "RAG評価": { path: "/rag/evaluation/" },
  // 監査・トレース
  "Agenticトレース一覧": { path: "/agents/" },
  "Agenticトレース詳細": { path: `/agents/${DUMMY_UUID}/`, needsId: true },
  "操作ログ": { path: "/audit/operations/" },
  "フィードバック": { path: "/audit/feedback/" },
  "フィードバック登録": { path: "/audit/feedback/new/", formPath: "/audit/feedback/new/" },
  // 管理・設定
  "案件一覧": { path: "/projects/" },
  "案件詳細": { path: `/projects/${DUMMY_UUID}/`, needsId: true },
  "外部連携": { path: "/integrations/" },
  "外部連携の追加": { path: "/integrations/new/", formPath: "/integrations/new/" },
  "外部連携の編集": { needsId: true },
  "接続確認の実行": { needsId: true },
  "同期の実行": { needsId: true },
  "同期の稼働状況": { path: "/integrations/pipeline/" },
  "同期履歴": { path: "/integrations/jobs/" },
  "AI設定": { path: "/core/settings/" },
  "画面マップ": { path: "/core/screen-map/" },
};

/** レスポンシブ横断ケースで開く画面（手順のキーワード → URL） */
export const RESPONSIVE_PATHS: Array<[RegExp, string]> = [
  [/ダッシュボード/, "/"],
  [/タスク一覧|ガント/, "/tasks/"],
  [/AI設定/, "/core/settings/"],
  [/フォーム/, "/projects/tasks/new/"],
];
