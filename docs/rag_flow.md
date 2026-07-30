# RAG / Agentic フロー

## 文書登録からの流れ

```text
① アップロード
   apps/documents/services/validation.py: validate_upload()
     形式（拡張子）／サイズ／空ファイル／重複ハッシュを検証
     → NG ならここで止め、Document を作らずに理由を返す
        ↓
② 文書台帳へ登録
   documents.Document (status=active)
        ↓
③ 本文抽出
   documents.DocumentPage（ページ／シート単位）
     旧 03.json/RES_*.json に相当。チャンク戦略を変えても再変換しなくてよい単位。
        ↓
④ チャンク分割 + ベクトル化
   apps/rag/services/indexer.py: rebuild_index()
     split_text()  文字数 800 / 重なり 120
     chunk_key()   文書ID・ページ・位置から決まる安定 ID
     get_embedder().embed()
        ↓
⑤ 保存
   rag.Chunk（本文・メタデータ）
   var/vector_store/<index_id>.jsonl（ベクトル実体）
```

### 再構築が全件入れ替えな理由

`rebuild_index()` は差分更新ではなく全再構築。Embedding モデルを切り替えたときに
古いベクトルと新しいベクトルが同じ空間に混ざると、検索結果が静かに壊れる。
まず正しさを優先している。チャンク数が増えて時間が問題になったら、
`VectorIndex.embedding_model` が一致する場合のみ差分更新、という条件付き最適化を入れる。

## 検索の流れ

```text
質問
 ├─▶ ベクトル検索   get_embedder().embed_one(質問) → cosine 類似度で上位 3N 件
 └─▶ 語彙検索       LexicalIndex.search()          → TF-IDF で上位 3N 件
              ↓
      Reciprocal Rank Fusion
        score = Σ 1 / (60 + rank)
              ↓
      RAG 対象のみに限定
        document.status == active かつ deleted_at is null
              ↓
      上位 N 件（既定 8）
```

スコアの合成に生の値ではなく順位を使うのは、cosine（-1〜1）と TF-IDF（非有界）で
スケールが揃わないため。旧実装が順位合成をしていたのと同じ理由。

検索結果は `RetrievalQuery` / `RetrievedChunk` として必ず保存する
（`search_and_record()`）。監査時に「この回答は何を見て書かれたか」を再現するため。

## Agentic RAG の流れ

`apps/agents/services/orchestrator.py: run()`

```text
① 意図分類          intent.classify()
     ルールベース。7 分類 + 確信度（low/medium/high → 0.3/0.6/0.9）
     旧 orchestrator.detect_pmo_intent() と同じ挙動
                    ↓
② 実行計画           build_plan()
     使うツール、検索クエリ、期待する出力を決める
     LLM 未設定なら LLM 必須ツール（rerank 等）を計画へ入れない
                    ↓
③ ツール実行         registry.get(name).func(...)
     search_local_docs → ハイブリッド検索
                    ↓
④ 根拠評価           evidence.evaluate()
     confidence = min(1, 根拠数/5) * 0.6 + 意図確信度 * 0.4
     → answer / answer_with_caution / ask_clarification
                    ↓
⑤ トレース保存       AgentRun + AgentStep + EvidenceEvaluation
```

### 根拠不足のときに何が起きるか

`EvidenceEvaluation.recommendation == ask_clarification` または `has_conflict` の場合、
`blocks_approval` が True になる。これが立っていると
`Deliverable.can_request_approval` が False を返し、成果物を承認へ回せない。

画面上も「根拠が不足しています」と明示し、断定した回答として見せない。

### ループ上限

現在の実装は再検索ループを持たないため `loop_count` は常に 1。
再検索を入れるときは `settings.AGENT["MAX_LOOPS"]`（既定 3）で必ず打ち切ること。
仕様書 NFR-AG-002 の「無制限のエージェントループは対象外」に対応する。

## 状態の見せ分け

再構築ブリーフ 6-5「モデル呼出、検索失敗、タイムアウト、権限不足、文書未登録の状態を
UI で区別して案内する」への対応。

| 状態 | 判定 | 画面の表示 |
|---|---|---|
| インデックス未構築 | `VectorIndex` が無い | 再構築コマンドを案内 |
| 検索結果ゼロ | `hits == []` | 登録状況と語句の確認を促す |
| 根拠不足 | `evidence.blocks_approval` | 承認へ進めない旨を明示 |
| AI 未設定 | `is_provider_configured()` が False | local_hash へ退避し、計画から LLM ツールを除外 |
| 権限不足 | ロール判定 | ナビゲーションに項目を出さない |
| 未移植画面 | `NavItem.status != "ready"` | 「未移植」バッジ + 未実装ページ |
