# Streamlit 版からの移行

## 何を引き継ぎ、何を変えたか

| 旧実装 | Django 版 | 変更理由 |
|---|---|---|
| `05.app/pmo_agent_app.py`（約 18,700 行の単一ファイル） | 9 アプリへ分割 | 責務が混ざり、変更の影響範囲が読めなかった |
| `st.session_state` | DB + セッション | 再起動で消える状態に業務データを置いていた |
| `index_map.json` | `documents.Document` | 絶対パス（`C:\VeriRAG\...`）依存で環境を移せなかった |
| `04.faiss_index/chunks.json` | `rag.Chunk` | 全件読み込みが前提で、絞り込みができなかった |
| `07.feedback/*.jsonl` | `audit.OperationLog`, `Feedback`, `rag.RetrievalQuery` | 期間・利用者での集計ができなかった |
| `.streamlit/secrets.toml` の読み書き | 環境変数（読み取り専用） | UI から秘密値を書き換えられる設計は避けたい |
| `template_registry.json` | `documents.Template` | 同上 |
| FAISS `IndexFlatIP` | `BaseVectorStore` 抽象 + JSONL | 依存を減らし、差し替え可能にした |
| ハードコードされた管理パスワード | Django 認証 + ロール | 認証情報をコードに置かない |

## 挙動を保った箇所

再現性が要件になっている部分は、旧実装のロジックをそのまま移植している。

| 機能 | 旧 | 新 | 保証 |
|---|---|---|---|
| トークン化 | `retrieval.tokenize_text()` | `rag/services/tokenizer.tokenize()` | 日本語 3 文字以上の bi-gram 展開まで同一 |
| 語彙スコア | `retrieval.lexical_score_candidates()` | `rag/services/lexical.LexicalIndex` | TF-IDF の式と idf の平滑化が同一 |
| 意図分類 | `orchestrator.detect_pmo_intent()` | `agents/services/intent.classify()` | キーワード、加点ルール、優先順位、確信度の閾値が同一 |
| 確認観点 | `orchestrator._INTENT_VIEWPOINTS` | `agents/services/intent.VIEWPOINTS` | 同一 |
| 文書ステータス | `index_map.json` の status | `documents.DocumentStatus` | active / excluded / missing / error を踏襲 |
| ひな型の分離 | RAG 対象に含めない | 同左（`Template` は `Chunk` を持たない） | 同一方針 |

意図分類の挙動は `apps/agents/tests/test_orchestrator.py` で固定している。
ここを変えるときは、旧実装との差分を意識して変更すること。

## データ移行

旧環境のデータをそのまま入れる想定はしていない。理由は以下。

- `index_map.json` の `source` が旧環境の絶対パス依存
- FAISS インデックスの Embedding モデルが現在の設定と一致する保証がない
- `07.feedback/*.jsonl` は業務情報を含む可能性がある

移行する場合の手順:

1. 原本文書（`00.input/`）を新環境へアップロードし直す
2. `python manage.py rebuild_index --tenant <code>` でインデックスを作り直す
3. 履歴を移す場合は、匿名化と社内承認を経てから投入する

`docs/open_questions.md` の OQ-008 も参照。

## 旧実装のどこを見ればよいか

参照資料は `docs/reference/` に置いてある。

| 目的 | ファイル |
|---|---|
| 旧構成の全体像 | `legacy_current_architecture.md` |
| 関数一覧（460 件） | `legacy_app_function_inventory.md` |
| Agentic RAG の要件 | `Agentic-RAG_Spec.plan.req.md` |
| 機能とディレクトリの対応 | `mvp_scope_directory_mapping.csv`, `directory_extra_features.csv` |
| 再現時に維持する観点 | `AI_REBUILD_BRIEF.md` |
| UI の正 | `../screens/VeriRAG_PMO_Agent_MVP.html` |
