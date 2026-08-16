# Evaluation Plan

調査日: 2026-06-02

本計画は、PMO_AIエージェントのRAG品質とPMO実務支援品質を継続評価するための案です。今回の作業では評価実装、index再生成、API実行は行っていません。

## 評価対象

| 対象 | 評価したいこと | 根拠ファイル |
|---|---|---|
| Retrieval | 質問に必要な文書・チャンクが上位に出るか | `04.faiss_index/chunks.json`, `lexical_index.json`, `search_chunks()` |
| Rerank | LLM再順位付けで直接性が上がるか | `rerank_results_with_llm()` |
| Answer | 参照チャンクに基づき、未確認事項を分けて回答するか | `answer_question()` |
| RAG Chat | 会話履歴を踏まえつつ、最新質問の根拠を検索できるか | `process_rag_chat_prompt()`, `complete_rag_chat_pending()` |
| PMO Support | PMO判断、タスク、確認事項、成果物ドラフトへ落とせるか | `answer_pmo_support()`, `generate_pmo_recommendations()`, `build_pmo_deliverable()` |
| Template Output | 回答結果が適切なExcelひな型へ安全に出力されるか | `template_registry.json`, `generate_filled_template()` |

## RAG品質評価方針

RAG評価は、検索評価と回答評価を分けます。検索が外れているのに回答だけを評価すると原因が分からず、回答が悪いのに検索だけを評価しても実務品質を保証できません。

検索評価では以下を測ります。

- `Recall@k`: 期待文書または期待チャンクが上位k件に含まれるか。
- `MRR`: 最初の正解根拠が何位に出るか。
- `nDCG@k`: 複数根拠の重要度を反映した順位品質。
- `source_coverage`: 複数文書をまたぐ質問で、必要な文書カテゴリが揃うか。
- `missing_expected_source`: 期待文書が検索結果に出なかったケース。

回答評価では以下を測ります。

- `citation_coverage`: 回答内の主要主張が参照チャンクに結びつくか。
- `unsupported_claims`: 参照チャンクや明示された一般知識に支えられない主張がないか。
- `abstention_quality`: 資料上確認できない場合に、確認できないことを明示できるか。
- `actionability`: PM/PMOが次に動けるタスク、確認先、判断材料になっているか。
- `separation`: 事実、推測、一般知識、未確認事項が分離されているか。

PMO支援評価では、RAG回答に加えて以下を確認します。

- 意思決定パックに「決めること」「根拠」「未確認事項」「次アクション」が入るか。
- 成果物ドラフトに参照根拠と人が判断すべき事項が残るか。
- PMBOK一般観点が、ローカル文書の事実と混同されていないか。

## Golden Dataset案

Golden DatasetはLLMに自動生成させた正解ではなく、PM/PMOが根拠チャンクを確認して作るべきです。初期データは、現存する13件のPDF由来JSONと213チャンクから小さく始めます。

推奨スキーマ:

```json
{
  "id": "golden-001",
  "question": "テスト管理でPMOが確認すべき観点は何ですか？",
  "intent": "pmo_review_points",
  "expected_sources": ["D12_テスト管理.pdf"],
  "expected_chunk_ids": [],
  "expected_terms": ["テスト", "確認", "管理"],
  "required_answer_sections": ["要約", "登録情報から確認できること", "登録情報だけでは確認できないこと", "推奨アクション"],
  "allowed_general_knowledge": true,
  "must_abstain_if_missing": false,
  "notes": "期待チャンクIDはPMOレビューで後から確定する"
}
```

初期ケース案:

| ID | 質問 | 期待ソース候補 | 主な評価観点 |
|---|---|---|---|
| G01 | テスト管理でPMOが確認すべき観点は何ですか？ | `D12_テスト管理.pdf` | 単一文書検索、PMO観点化 |
| G02 | リスク管理で早期に確認すべき項目は何ですか？ | `D02_リスク管理.pdf` | リスク観点、未確認事項 |
| G03 | 課題管理と変更管理の違いを整理してください。 | `D03_課題管理.pdf`, `D04_変更管理.pdf` | 複数文書検索、比較 |
| G04 | レビュー管理で記録すべき情報を教えてください。 | `D11_レビュー管理.pdf` | 具体項目抽出 |
| G05 | 品質管理でPMOが報告すべき指標は何ですか？ | `D09_品質管理.pdf`, `D10_測定分析と指標値改善.pdf` | 複数ソースの統合 |
| G06 | 文書管理の運用で注意すべき点は何ですか？ | `D08_文書管理.pdf` | 実務アクション化 |
| G07 | データ授受管理で確認すべきリスクを挙げてください。 | `D13_データ授受管理.pdf` | リスク抽出 |
| G08 | 登録資料にない外部規程番号を答えてください。 | 期待ソースなし | 幻覚抑制、回答拒否 |
| G09 | 構成管理台帳に出力できる内容を整理してください。 | `D06_構成管理.pdf`, `template_registry.json` | RAGとテンプレート導線 |
| G10 | 週次PMO報告のドラフトを作ってください。 | 複数候補 | PMO成果物、根拠と推測の分離 |

`expected_chunk_ids` は、最初は空でもよいですが、評価の精度を上げるにはPM/PMOが実際の `chunks.json` から根拠チャンクを選び、段階的に埋める必要があります。

## 回帰テスト方針

### レベル1: 静的・構文チェック

- `py_compile` でPython構文を確認する。
- secrets値が平文でドキュメント、ログ、UI表示に出ないことを文字列テストする。
- JSON/JSONLの読込スキーマを検証する。

### レベル2: オフライン単体テスト

- `tokenize_text()` の日本語・英数字混在ケース。
- `split_text()` のチャンク長とoverlap。
- `lexical_score_candidates()` の語彙ヒット。
- `chunk_identifier()` の安定性。
- JSONL append/read の壊れた行への耐性。
- テンプレートマッピングのdry-run。

このレベルではOpenAI/Ollamaを呼ばず、EmbeddingとLLM出力はスタブ化します。

### レベル3: 読取専用インテグレーション

- `04.faiss_index/chunks.json` を読み、チャンク件数とメタデータキーを確認する。
- `lexical_index.json` の `chunk_count` と `chunks.json` 件数が一致することを確認する。
- `index_map.json` の `active` レコードとチャンクの `metadata.source` の整合性を確認する。
- `template_registry.json` のactiveテンプレートと実ファイル存在を確認する。

このレベルでもindex再生成はしません。

### レベル4: APIあり評価

ユーザー承認後にだけ実行します。

- Golden Datasetを使って `search_chunks()` を実行する。
- `use_rerank` ON/OFF、`use_query_expansion` ON/OFFを比較する。
- `answer_question()` の回答を保存し、引用・未確認事項・アクション性を評価する。
- 実行結果は `eval/results/*.jsonl` のような専用出力へ保存し、`07.feedback` と混ぜない。

## 幻覚、引用不足、検索漏れの検出方法

### 幻覚

検出ルール:

- 回答内の固有名詞、数値、規程名、期限、担当、手順が参照チャンクに存在するか確認する。
- 回答が「登録情報から確認できること」と「登録情報だけでは確認できないこと」を分けているか確認する。
- 期待ソースなしの質問で、断定回答していないか確認する。
- PMBOK一般知識を使う場合、ローカル資料の事実として書いていないか確認する。

自動判定だけでは限界があるため、初期はルール判定と人手レビューを併用します。

### 引用不足

検出ルール:

- 回答に `chunk_id`、ファイル名、ページ/シート/スライドのいずれかが含まれるか。
- 回答で参照された `chunk_id` が検索結果の `results` に存在するか。
- 主要セクションごとに少なくとも1つの根拠があるか。
- 「資料上は確認できないこと」に、根拠不足の説明があるか。

### 検索漏れ

検出ルール:

- Golden Datasetの `expected_sources` がTop-kに含まれるか。
- 複数文書比較質問で、必要な全ソースカテゴリが出ているか。
- `use_query_expansion` ON/OFFで期待ソースのRecallが改善または維持されるか。
- `rerank` 後に期待ソースが順位落ちしすぎていないか。

検索漏れが発生した場合は、原因を `embedding_miss`, `lexical_miss`, `active_filter`, `stale_registry`, `rerank_drop`, `query_formulation` に分類します。

## 評価結果の保存案

評価結果は履歴ログと分け、以下のような専用ファイルに保存する案です。

```text
eval/
  golden/
    rag_golden_v1.jsonl
  results/
    rag_eval_YYYYMMDD_HHMMSS.jsonl
  reports/
    rag_eval_report_YYYYMMDD.md
```

各評価結果には、質問、検索設定、モデル、期待ソース、実際のTop-k、回答、評価スコア、失敗分類、レビュー者コメントを含めます。

## 未確認事項

- 実APIを使ったRAG評価は未実施です。
- Golden Datasetの正解チャンクは未作成です。
- PM/PMOによる業務妥当性レビューは未実施です。
- 既存履歴JSONLを評価に使う承認と匿名化ルールは未確定です。
