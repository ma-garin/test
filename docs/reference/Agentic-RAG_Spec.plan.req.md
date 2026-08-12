# Agentic RAG Specification / Plan / Requirements

作成日: 2026-05-27  
対象プロジェクト: VeriRAG / PMO_AI  
対象ディレクトリ: `C:\VeriRAG_test`

---

## 1. 目的

VeriRAGを、単純な「検索して回答するRAG」から、PMO支援に適した **Agentic RAG** へ段階的に拡張する。

Agentic RAG化の目的は、AIが以下を自律的に判断できるようにすることである。

- 依頼内容の意図を分類する
- ローカルRAG、PMBOK一般知識、会話履歴、成果物生成のどれを使うべきか判断する
- 必要に応じて検索クエリを分解・拡張する
- 取得した根拠が十分か評価する
- 根拠不足、未確認事項、推測を分離する
- PMO業務に使える確認観点、次アクション、成果物ドラフトへ落とし込む

本ドキュメントは、実装前の共通認識、段階的な作業計画、受け入れ条件を定義する。

---

## 2. 背景

現行のVeriRAGには、Agentic RAGに近い要素がすでに存在する。

| 現行機能 | 実装状況 | Agentic RAGとの関係 |
|---|---:|---|
| FAISSベクトル検索 | 実装済み | RAG基盤 |
| lexical index | 実装済み | ハイブリッド検索 |
| ハイブリッド検索 | 実装済み | 検索品質向上 |
| 質問展開 / HyDE | 実装済み | 検索前の意図補正 |
| LLMリランキング | 実装済み | 検索結果評価 |
| RAG/一般情報の回答比率 | 実装済み | 回答方針制御 |
| チャット履歴 | 実装済み | 短期メモリ |
| PMO支援 / PMBOK観点 | 実装済み | 業務特化エージェントの土台 |
| 成果物ドラフト / 承認 | 実装済み | タスク実行支援 |
| フィードバック保存 | 実装済み | 将来の改善データ |

ただし、現時点では処理フローが固定的であり、AIが状況に応じて「計画」「ツール選択」「再検索」「自己評価」を行う構造にはなっていない。

---

## 3. Agentic RAGの定義

本プロジェクトにおけるAgentic RAGは、以下のように定義する。

> PMO業務の依頼に対して、AIが依頼意図を解釈し、必要な情報源と処理手順を選び、検索・評価・回答・成果物化を段階的に実行するRAGアーキテクチャ。

重要な点は、単に複数エージェントを増やすことではない。

VeriRAGでは、まず **単一のPMO Orchestrator** を導入し、既存RAG機能をツールとして扱う。マルチエージェント化は、単一オーケストレーターで必要性が明確になってから検討する。

---

## 4. 対象範囲

### 4.1 対象

- RAG検索
- チャットモード
- PMO支援
- PMOコーチ
- プロンプトライブラリ
- 支援エージェント
- 成果物・承認
- 回答履歴、チャット履歴、フィードバック

### 4.2 対象外

- PMI公式DBへの接続
- PMBOK本文の直接引用・再配布
- 外部Web検索の常時利用
- モバイルアプリ対応
- 完全自律でのファイル更新・削除
- 承認なしの成果物確定
- 無制限のエージェントループ

---

## 5. 現行構成の確認

### 5.1 主なファイル

| ファイル | 役割 |
|---|---|
| `05.app\pmo_agent_app.py` | Streamlitアプリ本体 |
| `05.app\rag_web_min.py` | 最小RAG検証画面 |
| `99.scripts\doc_registry.py` | 文書登録一覧作成 |
| `99.scripts\PDF2jsonLoader.py` | PDFからJSONへの変換 |
| `99.scripts\Office2jsonLoader.py` | OfficeファイルからJSONへの変換 |
| `99.scripts\build_faiss_min.py` | chunks生成、FAISS/lexical index作成 |
| `07.feedback\rag_answer_history.jsonl` | RAG回答履歴 |
| `07.feedback\rag_chat_history.jsonl` | RAGチャット履歴 |
| `07.feedback\pmo_chat_history.jsonl` | PMO相談履歴 |
| `07.feedback\feedback.jsonl` | ユーザーフィードバック |

### 5.2 現行インデックス状態

2026-05-27確認時点のローカルRAG状態:

- `index_map.json`: 16件
- active: 13件
- excluded: 1件
- deleted: 2件
- chunks: 213件
- embedding provider: OpenAI
- embedding model: `text-embedding-3-small`

---

## 6. 目標アーキテクチャ

```text
User
  |
  v
PMO Orchestrator
  |
  +-- Intent Classifier
  |     - 質問回答
  |     - PMO相談
  |     - 成果物作成
  |     - レビュー観点作成
  |     - リスク/課題/進捗整理
  |
  +-- Planner
  |     - どの情報源を使うか
  |     - 何回検索するか
  |     - 追加質問が必要か
  |
  +-- Tool Registry
  |     - search_local_docs
  |     - expand_query
  |     - rerank_results
  |     - get_pmbok_guidance
  |     - generate_checklist
  |     - draft_deliverable
  |     - self_check_answer
  |
  +-- Evidence Evaluator
  |     - 根拠十分性
  |     - 矛盾
  |     - 未確認事項
  |     - 一般情報との分離
  |
  +-- Response Composer
        - 回答
        - 根拠
        - 推測
        - 確認事項
        - 次アクション
        - 成果物ドラフト
```

---

## 7. 実装方針

### 7.1 基本方針

- 既存RAG処理を壊さない
- まず単一オーケストレーターで実装する
- 既存の `search_chunks()`、`answer_question()`、`answer_pmo_support()` を再利用する
- UI変更は最小限から始める
- Agentic処理の途中経過はログとして保存する
- 根拠、推測、一般情報、人間判断事項を必ず分離する
- ループ回数、コスト、待ち時間に上限を設ける

### 7.2 推奨する実装単位

初期実装では、既存の `pmo_agent_app.py` に以下の関数群を追加する。

```text
agentic_intent_classify()
agentic_build_plan()
agentic_execute_plan()
agentic_evaluate_evidence()
agentic_compose_answer()
agentic_save_trace()
render_agentic_trace_panel()
```

将来的にファイル肥大化が問題になった場合は、以下へ分割する。

```text
05.app\agentic_rag_core.py
05.app\agentic_rag_tools.py
05.app\agentic_rag_prompts.py
05.app\agentic_rag_trace.py
```

---

## 8. フェーズ計画

### Phase 0: 仕様固定

目的:

- Agentic RAG化の範囲を合意する
- 本ドキュメントを基準にする
- 既存機能の破壊を防ぐ

作業:

- 本ドキュメント作成
- 現行RAG/PMO機能の棚卸し
- 実装対象と対象外の明確化

完了条件:

- `Agentic-RAG_Spec.plan.req.md` が作成されている
- 実装方針に合意している

---

### Phase 1: Agentic基盤の追加

目的:

- 既存RAGの上に、計画と実行履歴を持つ薄いオーケストレーターを追加する

作業:

- intent分類を追加
- 実行計画JSONを生成
- ツール候補を定義
- agent traceをJSONL保存
- UIに「Agentic処理ログ」を表示

完了条件:

- 既存のRAG検索、チャットモード、PMO支援が従来通り動く
- Agentic処理をON/OFFできる
- 処理ステップが確認できる

---

### Phase 2: 検索計画とツール選択

目的:

- 依頼内容に応じて検索戦略を変える

作業:

- RAGが必要かどうかを判断する
- PMBOK一般知識だけでよいケースを判定する
- ローカルRAG検索が必要なケースを判定する
- 検索クエリを複数に分解する
- 検索結果が弱い場合に1回だけ再検索する

完了条件:

- 質問内容に応じて検索有無が変わる
- 検索クエリ、検索回数、使用ツールがtraceに残る
- ループは最大2回までに制限されている

---

### Phase 3: 根拠評価と自己チェック

目的:

- 回答前後に品質評価を行う

作業:

- 検索結果の根拠十分性を評価
- 根拠不足の場合は回答を制限
- 一般情報と登録情報の混同を検出
- 回答後にセルフチェック結果を保存

完了条件:

- 「根拠十分」「根拠不足」「追加確認必要」が判定される
- 登録情報と一般情報が分離される
- 低信頼回答には注意文が出る

---

### Phase 4: PMO業務特化

目的:

- PMOコーチをAgentic RAGの主対象にする

作業:

- PMO依頼を業務カテゴリへ分類
- 進捗、課題、リスク、品質、変更、レビュー、報告にルーティング
- PMBOK領域とローカル資料の両方を使って推奨アクションを生成
- 成果物ドラフト生成へ自然につなげる

完了条件:

- PMO支援でAgentic実行が使える
- 依頼分類、PMBOK領域、検索根拠、推奨タスクがtraceに残る
- 成果物・承認画面との連携が維持される

---

### Phase 5: 履歴・フィードバック活用

目的:

- 過去の会話、回答評価、フィードバックを次回の判断に利用する

作業:

- 高評価/低評価回答の傾向を保存
- 低評価理由を次回プロンプトに反映
- よく使われる依頼を推奨プロンプト候補にする
- チャット履歴から文脈を要約して使う

完了条件:

- フィードバックが単なる保存で終わらない
- 回答改善に使われる
- ユーザーに見える形で「参考にした履歴」を表示する

---

## 9. 機能要件

### REQ-AG-001: Agenticモード切替

RAG検索、チャットモード、PMO支援でAgentic処理をON/OFFできること。

### REQ-AG-002: 意図分類

依頼を以下のいずれかに分類できること。

- fact_qa
- pmo_advice
- risk_analysis
- issue_analysis
- progress_review
- quality_review
- stakeholder_report
- deliverable_draft
- learning
- unknown

### REQ-AG-003: 実行計画生成

回答前に、以下を含む実行計画を内部生成すること。

- intent
- tools
- search_required
- search_queries
- expected_output
- risk_notes

### REQ-AG-004: ツール選択

以下のツールを内部的に選択できること。

- `search_local_docs`
- `expand_query`
- `rerank_results`
- `get_pmbok_guidance`
- `summarize_chat_history`
- `generate_checklist`
- `draft_deliverable`
- `self_check_answer`

### REQ-AG-005: 再検索

根拠不足の場合、最大1回まで検索クエリを見直して再検索できること。

### REQ-AG-006: 根拠評価

検索結果に対して以下を評価すること。

- relevance
- coverage
- conflict
- missing_information
- confidence

### REQ-AG-007: 回答構造

Agentic回答は、原則として以下の構造を持つこと。

```text
## 判断サマリ
## 登録情報から確認できること
## PMBOK / 一般情報による補足
## 資料上は確認できないこと
## 推奨アクション
## 追加確認事項
## 参照根拠
```

### REQ-AG-008: Trace保存

Agentic処理の実行履歴をJSONLで保存すること。

保存先案:

```text
07.feedback\agentic_trace.jsonl
```

### REQ-AG-009: Trace表示

UI上で、必要に応じて以下を確認できること。

- 分類結果
- 実行計画
- 使用ツール
- 検索クエリ
- 参照チャンク
- 評価結果
- 所要時間

### REQ-AG-010: 人間判断の明示

AIが確定してはいけない事項は、人間判断事項として明示すること。

---

## 10. 非機能要件

### NFR-AG-001: 既存機能互換

AgenticモードOFF時は、既存のRAG検索、チャットモード、PMO支援が従来通り動作すること。

### NFR-AG-002: ループ制限

自律処理のループ回数は最大2回とする。

### NFR-AG-003: コスト制御

LLM呼び出し回数をtraceに保存し、不要な多段呼び出しを避けること。

### NFR-AG-004: レイテンシ

通常の質問では、検索・回答・評価を含めて実用的な待ち時間に収めること。

初期目安:

- 軽量質問: 30秒以内
- PMO支援: 60秒以内
- 成果物ドラフト: 90秒以内

### NFR-AG-005: 透明性

AIがなぜその回答に至ったかを、ユーザーが確認できること。

### NFR-AG-006: 安全性

ファイル削除、登録状態変更、承認確定などの操作を、AIが自動実行しないこと。

---

## 11. データ設計案

### 11.1 Agentic Trace

```json
{
  "id": "20260527123000000000",
  "time": "2026-05-27T12:30:00",
  "area": "rag_chat",
  "session_id": "chat-session-id",
  "user_input": "...",
  "intent": "pmo_advice",
  "plan": {
    "search_required": true,
    "tools": ["expand_query", "search_local_docs", "rerank_results", "self_check_answer"],
    "search_queries": ["..."],
    "expected_output": "..."
  },
  "steps": [
    {
      "name": "intent_classify",
      "status": "ok",
      "elapsed": 1.2
    }
  ],
  "evidence": {
    "confidence": 0.78,
    "coverage": "medium",
    "missing_information": ["..."]
  },
  "answer_settings": {
    "ai_provider": "openai",
    "answer_model": "gpt-5.5"
  },
  "elapsed_total": 35.4
}
```

### 11.2 Evidence Evaluation

```json
{
  "confidence": 0.0,
  "relevance": "low | medium | high",
  "coverage": "low | medium | high",
  "has_conflict": false,
  "missing_information": [],
  "recommendation": "answer | ask_clarification | answer_with_caution"
}
```

---

## 12. UI方針

### 12.1 基本

- 既存UIを大きく崩さない
- Agentic処理は主画面を圧迫しない
- 詳細は折りたたみ表示にする
- ユーザーが見たい時だけ処理ログを確認できるようにする

### 12.2 表示案

```text
回答画面
├─ 回答本文
├─ 参照根拠
└─ Agentic処理ログ
   ├─ 意図分類
   ├─ 実行計画
   ├─ 使用ツール
   ├─ 検索クエリ
   └─ 根拠評価
```

---

## 13. 検証方針

### 13.1 静的検証

- `py_compile` が通ること
- 既存関数のシグネチャ破壊がないこと
- JSONL保存形式が壊れていないこと

### 13.2 機能検証

- Agentic OFFで従来回答が動くこと
- Agentic ONでtraceが保存されること
- 検索不要な質問で検索を省略できること
- 根拠不足時に注意付き回答になること
- PMO相談でPMBOK観点とローカル根拠が分離されること

### 13.3 画面検証

UI変更を伴う場合は、スクリーンショットで確認する。

現時点でCodex側から `http://localhost:8502/` の実画面確認は安定して行えていないため、以下のいずれかで確認する。

- ユーザー側スクリーンショット確認
- Playwright導入によるブラウザ操作検証
- 実装前のHTML/CSSモック確認

画面を確認できていない場合は、完了報告で「画面表示は未検証」と明記する。

---

## 14. 受け入れ条件

### Phase 1受け入れ条件

- AgenticモードON/OFFがある
- ON時に分類、計画、使用ツール、根拠評価が保存される
- OFF時に既存動作が維持される
- `py_compile` が成功する

### Phase 2受け入れ条件

- 検索が必要な質問と不要な質問を分けられる
- 検索クエリの分解がtraceに残る
- 最大1回の再検索が実行できる
- 無制限ループがない

### Phase 3受け入れ条件

- 回答後の自己チェックが保存される
- 根拠不足時に回答が慎重になる
- 登録情報、一般情報、推測が分離される

### Phase 4受け入れ条件

- PMO支援でAgentic処理が使える
- PMBOK領域とローカル根拠の使い分けができる
- 成果物・承認への導線が維持される

---

## 15. 実装優先順位

| 優先 | 項目 | 理由 |
|---:|---|---|
| 1 | Agentic trace保存 | 透明性と検証の基盤 |
| 2 | intent分類 | 以降の分岐の起点 |
| 3 | 実行計画生成 | Agentic RAGの中核 |
| 4 | 根拠評価 | 品質担保に必要 |
| 5 | PMO支援への適用 | 本システムの価値に直結 |
| 6 | フィードバック活用 | 改善サイクル |
| 7 | マルチエージェント化 | 必要性が明確になってから |

---

## 16. リスクと対策

| リスク | 内容 | 対策 |
|---|---|---|
| 複雑化 | Agentic化で処理が追いにくくなる | trace保存、ON/OFF、段階導入 |
| コスト増 | LLM呼び出しが増える | ループ上限、軽量分類、設定表示 |
| 遅延 | 回答までの時間が延びる | 検索不要判定、再検索上限 |
| 誤った計画 | 間違ったツール選択をする | self-check、人間判断事項の明示 |
| 根拠混同 | ローカル根拠と一般論が混ざる | 回答フォーマットで分離 |
| UI悪化 | 情報量が増えすぎる | 詳細ログは折りたたみ |

---

## 17. 未決事項

- AgenticモードをRAG検索とPMO支援の両方に同時導入するか
- 先にPMO支援だけに限定するか
- trace表示をどの画面に置くか
- フィードバックをどのタイミングでプロンプトへ反映するか
- Playwrightを導入して画面検証を自動化するか

---

## 18. 推奨する次アクション

次の実装ステップは、以下を推奨する。

1. `agentic_trace.jsonl` の保存関数を追加する
2. `agentic_intent_classify()` を追加する
3. `agentic_build_plan()` を追加する
4. RAGチャットモードにAgentic ON/OFFを追加する
5. Agentic ON時だけ、分類・計画・trace保存を実行する
6. 既存回答生成はまだ変更しない

これにより、まず「見えるAgentic化」を行い、回答品質への影響を最小にした状態で次段階へ進める。

