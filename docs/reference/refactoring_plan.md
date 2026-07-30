# Refactoring Plan

調査日: 2026-06-02

本計画は、PMO_AIエージェントを「RAG検索アプリ」から「PMO実務支援エージェント」へ進化させるための段階的な整理案です。今回の作業では実装変更は行っていません。

## 基本方針

- 最初は挙動を変えず、責務境界とテスト可能性を作る。
- `05.app/pmo_agent_app.py` を直接大改修しない。
- `C:\VeriRAG_test` で検証し、受け入れ後に変更済みapp/scriptファイルだけ本番へ反映する。
- secrets、履歴、インデックス、原本データは明示依頼がない限り触らない。
- RAG品質に関わる変更はGolden Dataset評価を先に作ってから行う。

## 変更優先順位

| 優先度 | テーマ | 理由 |
|---|---|---|
| P0 | 設定・secrets・管理削除の安全境界 | ハードコード管理パスワード、secretsバックアップ、物理削除は事故時の影響が大きい |
| P0 | `index_map.json` と環境分離の保護 | 検証台帳が本番パスを参照しており、取込・再構築時に混乱しやすい |
| P1 | RAG検索処理のモジュール化 | 検索品質評価、rerank、クエリ拡張をテスト可能にするため |
| P1 | 履歴・フィードバックの保存契約整理 | 評価データ化、匿名化、回帰テストに必要 |
| P2 | テンプレート出力の分離 | Excel入出力とRAG回答ペイロードの結合を弱めるため |
| P2 | PMO支援ロジックの分離 | PMO実務支援エージェントとして、判断材料・承認・成果物を独立進化させるため |
| P3 | Agentic RAGの実行ログ・評価接続 | 既存画面は枠中心のため、trace/evaluationを実処理へ接続する前に評価設計が必要 |

## 分割すべきモジュール案

将来的には `05.app/pmo_agent_app.py` から以下のようなモジュールへ段階的に移します。ファイル名は案です。

| モジュール | 切り出す責務 |
|---|---|
| `05.app/pmo_agent/config.py` | パス定義、対応拡張子、モデル選択肢、環境ラベル |
| `05.app/pmo_agent/settings.py` | secrets読み書き、マスク、OpenAI/Ollama設定 |
| `05.app/pmo_agent/storage.py` | JSON/JSONL読み書き、操作ログ、履歴共通処理 |
| `05.app/pmo_agent/registry.py` | `index_map.json` の読込、保存、状態遷移、フィルタ |
| `05.app/pmo_agent/ingestion.py` | activeリスト作成、PDF/Office変換、FAISS再構築の呼び出し |
| `05.app/pmo_agent/retrieval.py` | tokenization、lexical index、FAISS読込、hybrid search、rerank |
| `05.app/pmo_agent/generation.py` | RAG回答、PMO回答、クエリ拡張、LLM呼び出し |
| `05.app/pmo_agent/history.py` | RAG回答履歴、RAGチャット履歴、PMO履歴、Outcome履歴 |
| `05.app/pmo_agent/templates.py` | ひな型登録、Excelメタデータ、出力マッピング、出力履歴 |
| `05.app/pmo_agent/pmo.py` | PMO支援モード、推奨タスク、意思決定パック、成果物ドラフト |
| `05.app/pmo_agent/admin.py` | 管理者認証、削除安全確認、削除対象検証 |
| `05.app/pmo_agent/ui/*` | Streamlit描画。業務ロジックを呼ぶだけにする |

## 最初の3PR

### PR1: 安全境界と共通ストレージの切り出し

目的: 挙動を変えず、設定・secrets・JSONL・パスの責務を小さなモジュールへ移す。

スコープ:

- `config.py`, `settings.py`, `storage.py` を追加する。
- `PROJECT_ROOT`, `SECRETS_PATH`, `FEEDBACK_DIR` などのパス定義を集約する。
- `load_app_secrets()`, `save_app_secrets()`, `mask_secret()`, `append_jsonl()`, `read_jsonl()`, `append_operation_log()` を移す。
- secrets保存時のバックアップ方針を明文化し、少なくとも平文値がログへ出ないことをテストする。
- 既存 `pmo_agent_app.py` は移設先関数を呼ぶだけにする。

Non Goal:

- UIデザイン変更はしない。
- RAG検索順位は変えない。
- index再生成はしない。
- 本番コピーはしない。

受け入れ条件:

- `pmo_agent_app.py` の画面遷移が従来どおり動く。
- APIキー表示はマスクされたまま。
- `py_compile` が通る。
- `storage.py` のJSONL読み書きテストが通る。
- `.streamlit/secrets.toml` の中身をテストログや標準出力に出さない。

### PR2: Retrievalコアの切り出しと読取専用回帰テスト

目的: RAG検索の品質を壊さず、FAISS/語彙検索/rerankを独立テスト可能にする。

スコープ:

- `retrieval.py` を追加する。
- `tokenize_text()`, `chunk_identifier()`, `load_lexical_index()`, `lexical_score_candidates()`, `load_rag_resources()`, `hybrid_search_chunks()`, `search_chunks()` を移す。
- `generation.py` に `generate_retrieval_queries()` と `rerank_results_with_llm()` を分離するか、PR2内では薄い依存として残す。
- 既存の `04.faiss_index/chunks.json` と `lexical_index.json` を読むだけのスモークテストを追加する。
- APIを使わない単体テストではEmbedding結果をスタブ化する。

Non Goal:

- Embeddingモデル変更はしない。
- FAISS indexを再作成しない。
- Rerankプロンプトの改善はしない。
- PMO支援出力は変更しない。

受け入れ条件:

- 既存インデックスを使った読取専用チェックで、チャンク数213件を読める。
- `lexical_index.json` の `chunk_count` と `chunks.json` 件数が一致する。
- スタブEmbeddingを使った検索単体テストが通る。
- `pmo_agent_app.py` からRAG検索/回答/チャットが従来どおり呼べる。
- 例外時にsecrets値をログ出力しない。

### PR3: PMO支援と履歴の評価ハーネス接続

目的: PMO実務支援エージェントとして、RAG回答、PMO回答、履歴、フィードバックを評価できる状態にする。

スコープ:

- `history.py` にRAG回答履歴、RAGチャット履歴、PMO履歴、Outcome履歴を移す。
- `pmo.py` にPMO支援モード、推奨タスク、意思決定パック、手順、成果物ドラフト生成を移す。
- `eval/` または `tests/evaluation/` にGolden Datasetのスキーマとサンプルを追加する。
- 既存履歴JSONLから評価候補を抽出する際の匿名化ルールを作る。
- Agentic RAG画面の評価タブに、まずは静的な評価結果JSONを読み込む導線を作る。

Non Goal:

- 自律的なAgentic実行はまだ実装しない。
- PMBOK本文や外部有償文書を取り込まない。
- テンプレート出力のExcelレイアウト変更はしない。
- 既存履歴の破壊的マイグレーションはしない。

受け入れ条件:

- 既存RAG回答履歴、RAGチャット履歴、PMO履歴が読める。
- Golden Datasetのスキーマ検証が通る。
- 評価結果に `retrieval_recall`, `citation_coverage`, `unsupported_claims`, `missing_expected_source` を出せる。
- PMO支援回答で、ローカル参照情報と一般知識の区別が評価対象として記録される。
- 既存UIから見た履歴表示は従来どおり。

## 進め方の注意

各PRは「移動して呼び出し先を変える」ことを基本にし、同時にロジック改善を混ぜないでください。改善を混ぜる場合は、Golden Dataset評価で変更前後の差分を出せる状態にしてから行うべきです。

`C:\VeriRAG_test\index_map.json` は本番パスを参照しているため、取込・再構築・削除を伴うPRでは、最初に台帳と対象ファイルのルートを表示し、ユーザー承認を得る運用にしてください。

## 未確認事項

- 既存UIの実ブラウザ挙動は未確認です。
- テストフレームワークの標準は未定です。
- 本番側 `C:\VeriRAG` の現行差分は未比較です。
- 履歴JSONLを評価データへ転用してよいかは未承認です。

## ChangeSet-007: RAG検索境界レビュー追記

このプロジェクトではGitを使わないため、以降の実作業はPRではなくChangeSet単位で進めます。

### 境界レビュー結果

現行RAG検索の中心は `05.app/pmo_agent_app.py` の `search_chunks()` であり、内部で以下を呼び出します。

1. `generate_retrieval_queries()` による任意の質問展開
2. `hybrid_search_chunks()` によるEmbedding、FAISS検索、lexical検索、RRF統合、active source絞り込み
3. `rerank_results_with_llm()` による任意のLLM再順位付け

回答生成は `answer_question()` / `answer_pmo_support()` が検索結果から `context_text` を組み立て、promptへ渡しています。したがって、retrieval分離時に回答生成promptやrerank promptを同時に触ると、検索差分と生成差分が混ざります。

### ChangeSet-008で切り出す候補

ChangeSet-008では、まずAPIなし・promptなし・UIなしの検索補助層だけを `05.app/pmo_agent/retrieval.py` へ切り出す方針が安全です。

- `TOKEN_PATTERN`
- `tokenize_text()`
- `chunk_identifier()`
- `load_lexical_index()`
- `lexical_score_candidates()`
- `result_from_chunk()` の検索結果dict整形部分

`search_chunks()` は既存UIから呼ばれるpublic入口として残し、まずは同名wrapperまたはimport先変更だけに留めます。

### retrieval分離時の注意点

- `hybrid_search_chunks()` は検索順位の中核であり、Embedding API、FAISS、lexical、RRF、active絞り込みを同時に扱うため初回切り出しでは触らない。
- `load_rag_resources()` は `@st.cache_resource` とsecrets/FAISS/chunks読み込みが結合しているため、cache境界を変えない。
- `generate_retrieval_queries()` と `rerank_results_with_llm()` はpromptとAPI呼び出しを含むため、retrieval初回分離では触らない。
- `answer_question()` と `answer_pmo_support()` のpromptは変更しない。
- RAGチャット、PMOコーチ、履歴保存、フィードバック、テンプレート出力はUI/副作用層として残す。

### 検索順位維持の評価条件

ChangeSet-008後は、最低限以下が変更前と一致することを受け入れ条件にします。

- `python eval/run_static_checks.py`: PASS
- `python eval/run_offline_retrieval_eval.py --output-dir C:\VeriRAG_test\eval_local_results`: PASS
- Mean source recall@10: `0.9444`
- Mean chunk recall@10: `0.8144`
- Mean source-forced chunk recall@10: `1.0`
- source-forced missing expected chunks@10: `none`
- `python eval/run_api_answer_eval.py --dry-run --case-id G01 --case-id G08`: PASS

詳細は `eval/reports/rag_retrieval_boundary_review.md`、`eval/reports/rag_extraction_plan.md`、`eval/reports/rag_function_inventory.csv` を参照してください。
