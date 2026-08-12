# ドキュメント一覧

## 設計（このリポジトリで書いたもの）

| ファイル | 内容 |
|---|---|
| `architecture.md` | 責務分割、アプリ境界、差し替え可能にした箇所 |
| `domain_model.md` | 概念データモデルと実装テーブルの対応、設計判断 |
| `rag_flow.md` | 文書登録 → 索引 → 検索 → Agentic 実行の流れ |
| `screen_map.md` | 画面一覧と移植状況、画面ごとの受入条件 |
| `api_contract.md` | エンドポイント一覧、応答の約束、API 化の方針 |
| `requirements/p0_scope.md` | P0/P1 のスコープ、実装済み／未実装、推奨する実装順序 |
| `requirements/traceability.md` | 旧実装の機能一覧と実装状況の対応（要件トレーサビリティ） |
| `open_questions.md` | 未確定事項 |
| `migration/from_streamlit.md` | 旧実装から何を引き継ぎ、何を変えたか |
| `adr/` | アーキテクチャ決定記録 |
| `idea/sidebar_variants.html` | UI 検討中の試作（サイドバー案） |
| `INCIDENT-001-scope-omission.md` | インシデント記録（スコープ突合せの漏れ） |

## 参照資料（元の資料。編集しない）

| ファイル | 内容 |
|---|---|
| `reference/AI_REBUILD_BRIEF.md` | 再現参照パックの読み方 |
| `reference/Agentic-RAG_Spec.plan.req.md` | Agentic RAG の仕様・計画・要件（REQ-AG-001〜010） |
| `reference/legacy_current_architecture.md` | 旧構成の調査メモ |
| `reference/legacy_readme.md` | 旧 README |
| `reference/legacy_app_function_inventory.md` | 旧実装の関数一覧（460 件） |
| `reference/mvp_scope_directory_mapping.csv` | MVP 機能と旧実装の対応 |
| `reference/directory_extra_features.csv` | 旧実装にある追加機能 |
| `reference/evaluation_plan.md` / `evaluation_runbook.md` | 評価計画・手順 |
| `reference/refactoring_plan.md` / `technical_debt.md` | 旧実装のリファクタ計画・技術的負債 |
| `screens/VeriRAG_PMO_Agent_MVP.html` | **UI の正**。画面デザイン・導線のモック |
| `screens/*.html`, `screens/*.png` | 進捗スライド、スクリーンショット |

## 読む順序

1. `README.md`（リポジトリ直下）— 何ができるか、どう動かすか
2. `requirements/p0_scope.md` — 何が実装済みで、次に何をやるか
3. `architecture.md` → `domain_model.md` — 構造
4. `rag_flow.md` — AI 周りの動き
5. `screens/VeriRAG_PMO_Agent_MVP.html` — 目指す画面
6. `open_questions.md` — 決まっていないこと
