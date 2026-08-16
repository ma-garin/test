# Current Architecture

調査日: 2026-06-02

本ドキュメントは `C:\VeriRAG_test` の現状調査メモです。アプリ本体、既存データ、FAISSインデックス、`.streamlit/secrets.toml` は変更していません。秘密値の内容も表示していません。

## 根拠ファイル

| 区分 | ファイル | 確認した内容 |
|---|---|---|
| 作業ルール | `AGENTS.md` | 検証環境、本番反映ルール、重要ファイル、secrets非開示 |
| 環境分離 | `README_ENV.md`, `C:\VeriRAG\README_ENV.md` | 本番 `C:\VeriRAG` は 8501、検証 `C:\VeriRAG_test` は 8502、Pythonは本番仮想環境を共有 |
| 既存Readme | `Readme.md` | PDF/Office to JSON to FAISS to Streamlit の意図。ただし表示上の文字化けが多く、詳細根拠にはしない |
| メインアプリ | `05.app/pmo_agent_app.py` | Streamlit UI、文書管理、RAG検索、RAGチャット、PMO支援、履歴、テンプレート、設定、管理画面 |
| 最小RAG | `05.app/rag_web_min.py` | OpenAI + FAISS の最小検索・回答画面 |
| 取込 | `99.scripts/doc_registry.py`, `PDF2jsonLoader.py`, `Office2jsonLoader.py` | 文書台帳更新、PDF/OfficeのJSON化 |
| インデックス | `99.scripts/build_faiss_min.py` | JSONチャンク化、Embedding、FAISS/lexical index作成 |
| データ | `index_map.json`, `04.faiss_index/chunks.json`, `04.faiss_index/lexical_index.json`, `template_registry.json`, `07.feedback/*.jsonl` | 現在の台帳、検索対象チャンク、履歴、テンプレート状態 |

## 現在の構成

`C:\VeriRAG_test` は検証環境で、起動スクリプトは `start_test.ps1` です。`start_test.ps1` は `C:\VeriRAG_test` をカレントにし、`C:\VeriRAG\.venv\Scripts\python.exe -m streamlit run C:\VeriRAG_test\05.app\pmo_agent_app.py --server.port 8502` を実行します。

主要ディレクトリは以下です。

| パス | 役割 |
|---|---|
| `00.input/pdf` | RAG対象PDFの原本 |
| `00.input/office` | RAG対象Officeファイルの原本 |
| `00.input/templates` | RAG対象とは分離されたExcelひな型原本 |
| `03.json` | PDF/Officeから変換された `RES_*.json` |
| `04.faiss_index` | `index.faiss`, `chunks.json`, `lexical_index.json` |
| `05.app` | Streamlitアプリ |
| `06.list` | 変換対象ファイル一覧 |
| `07.feedback` | RAG回答履歴、チャット履歴、PMO履歴、操作ログ、テンプレート出力履歴 |
| `99.scripts` | 台帳更新、変換、FAISS構築、最小RAG検証スクリプト |
| `docs` | 今回追加する設計・評価ドキュメント |

現時点の確認結果では、`index_map.json` は16件あり、状態は `active` 13件、`deleted` 2件、`excluded` 1件です。全16件の `source` は `C:\VeriRAG\...` 参照で、`C:\VeriRAG_test\...` 参照はありません。既存ルールにもある通り、検証環境で取込を行う前に台帳パスの確認が必要です。

`04.faiss_index/chunks.json` は213チャンクで、確認時点ではすべてPDF由来です。`lexical_index.json` は `openai / text-embedding-3-small` で作成されたメタ情報を持ち、213チャンク分の語彙索引を保持しています。

## データフロー

現在の文書取込から回答までの流れは次の通りです。

```text
00.input/pdf, 00.input/office
  -> 99.scripts/doc_registry.py
  -> index_map.json
  -> pmo_agent_app.py が active レコードから 06.list/*.txt を作成
  -> PDF2jsonLoader.py / Office2jsonLoader.py
  -> 03.json/RES_*.json
  -> build_faiss_min.py
  -> 04.faiss_index/index.faiss
  -> 04.faiss_index/chunks.json
  -> 04.faiss_index/lexical_index.json
  -> pmo_agent_app.py の RAG検索 / RAGチャット / PMO支援
  -> 07.feedback/*.jsonl
```

ひな型は通常のRAG対象とは別系統です。

```text
00.input/templates/*.xlsx
  -> template_registry.json
  -> RAG検索結果 / RAG回答 / RAGチャット回答の右ペイン
  -> 原本コピーまたは一般ひな型Workbookを生成
  -> 07.feedback/template_outputs/*.xlsx
  -> 07.feedback/template_output_history.jsonl
```

## 主要ファイルの責務

### `05.app/pmo_agent_app.py`

約18,730行、657KBの単一Streamlitアプリです。確認できた責務は以下です。

- アプリ内パス定義: `PROJECT_ROOT`, `INDEX_MAP_PATH`, `FAISS_DIR`, `FEEDBACK_DIR`, `TEMPLATE_REGISTRY_PATH`, `SECRETS_PATH`
- Streamlit画面: ダッシュボード、ドキュメント管理、RAG検索、PMO支援、フィードバック、設定、管理者画面
- AI設定: OpenAI/Ollamaプロバイダー、回答モデル、Embeddingモデル、secrets読み書き、マスク表示
- RAG検索: FAISS読込、Embedding、語彙索引、ハイブリッド検索、クエリ拡張、LLM rerank
- 回答生成: RAG回答、RAGチャット、PMO支援回答
- 履歴: RAG回答履歴、RAGチャットセッション、PMO相談履歴、PMO Outcome、操作ログ
- テンプレート: Excelひな型登録、メタデータ抽出、AI支援マッピング、Excel出力
- 管理: 管理者ログイン、物理削除、FAISS出力削除

### `05.app/rag_web_min.py`

`.streamlit/secrets.toml`、`04.faiss_index/index.faiss`、`04.faiss_index/chunks.json` を読み、OpenAI EmbeddingでFAISS検索し、OpenAI Responses APIで回答する最小検証画面です。PMO支援、履歴、テンプレート、ハイブリッド検索は含みません。

### `99.scripts/doc_registry.py`

`00.input` 配下を走査し、PDF/Officeファイルを `index_map.json` に登録します。対応拡張子は `.pdf`, `.xlsx`, `.xlsm`, `.xls`, `.docx`, `.doc`, `.pptx` です。既存レコードの `excluded` / `deleted` 状態は維持し、未検出の既存レコードは `missing` へ移します。

### `99.scripts/PDF2jsonLoader.py`

`06.list/pdf_list.txt` のPDFをPyMuPDFでページ単位に読み、`03.json/RES_<stem>.json` を作成します。各itemは `page`, `content`, `metadata.source`, `metadata.file_name`, `metadata.file_stem` を持ちます。

### `99.scripts/Office2jsonLoader.py`

`06.list/office_list.txt` のOfficeファイルをJSON化します。`.xlsx/.xlsm` はopenpyxl、`.docx` はpython-docx、`.pptx` はpython-pptx、`.xls/.doc` はpywin32で一時変換して処理します。出力は `03.json/RES_<stem>.json` です。

### `99.scripts/build_faiss_min.py`

`03.json/RES_*.json` をチャンク化し、OpenAIまたはOllamaのEmbeddingでベクトル化します。FAISSは `IndexFlatIP` を使い、L2正規化後に `04.faiss_index/index.faiss` へ保存します。同時に `chunks.json` と `lexical_index.json` を作成します。`--index-map` 指定時は `index_map.json` の `active` ソースに限定します。

## RAG処理の流れ

メインアプリのRAG検索は以下の順に処理します。

1. `get_ai_settings()` / `get_answer_settings()` でOpenAIまたはOllamaのモデル設定を取得する。
2. `load_rag_resources()` が `index.faiss` と `chunks.json` をキャッシュ読込する。
3. 必要に応じて `generate_retrieval_queries()` がLLMで検索クエリを拡張する。
4. `hybrid_search_chunks()` が各クエリでEmbeddingを作成し、FAISS検索を行う。
5. 同じクエリを `lexical_score_candidates()` で語彙検索する。
6. ベクトル順位と語彙順位を合成し、`index_map.json` 上の `active` レコードに限定する。
7. `use_rerank=True` の場合、`rerank_results_with_llm()` が上位候補をLLMで再順位付けする。
8. `answer_question()` が参照チャンク、会話履歴、回答バランス、テンプレート文脈をプロンプトに入れて回答する。
9. `save_rag_answer_history()` または `save_rag_chat_session()` がJSONLへ履歴を保存する。

PMO支援では、RAG検索結果を必要に応じて取得し、`answer_pmo_support()` がPMBOK一般観点とローカル参照情報を分けて回答します。さらに `generate_pmo_recommendations()`, `build_pmo_decision_pack()`, `generate_pmo_procedures()`, `build_pmo_deliverable()` がタスク、判断材料、手順、成果物ドラフトを組み立てます。

## UI / 検索 / 生成 / 履歴 / テンプレートの関係

`MAIN_MENU_OPTIONS` は、ダッシュボード、ドキュメント管理、RAG検索、PMO支援、フィードバック、設定です。RAG検索配下には検索画面とチャットモードがあります。PMO支援配下にはTOP、PMOコーチ、プロンプトライブラリ、支援エージェント、成果物・承認があります。

RAG検索画面は `Answer`, `Search`, `Brief` のタブ構成です。`Search` は検索結果だけを表示し、右ペインのひな型出力に渡せます。`Answer` は検索結果を使って回答を生成し、回答履歴とひな型出力の対象になります。

RAGチャットは、左にチャット履歴、中央に会話、右にRAG設定・参照情報を置く構成です。新規質問は `process_rag_chat_prompt()` でpending状態に入り、`complete_rag_chat_pending()` が検索、回答生成、履歴保存を行います。

テンプレート機能はRAG本文とは分離されています。`render_template_registry_page()` の説明どおり、ひな型は通常のRAG対象には入れず、回答結果の出力先として原本コピーに書き込む設計です。RAG検索、RAG回答、RAGチャットの各回答ペイロードから `render_template_output_panel()` に渡され、Excel出力が作成されます。

履歴とフィードバックは `07.feedback` 配下のJSONLです。確認時点で `rag_answer_history.jsonl`、`rag_chat_history.jsonl`、`pmo_chat_history.jsonl`、`operations.jsonl`、`template_output_history.jsonl` が存在します。履歴は評価データの候補になりますが、業務情報を含む可能性があるため、Golden Datasetへ採用する際は匿名化と承認が必要です。

## 現時点の未確認事項

- Streamlit画面を実起動しての操作確認は未実施です。
- OpenAI/Ollamaの実API疎通は未実施です。
- `.streamlit/secrets.toml` の値は未確認です。
- 既存FAISSインデックスと現在の `index_map.json` のソースパス整合性は未検証です。
- `Readme.md` と一部スクリプト出力は文字化けしており、正しい日本語原文は未確認です。
