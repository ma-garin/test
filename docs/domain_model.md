# 概念データモデル

再構築ブリーフ 5 章の概念と、実装テーブルの対応。

| ブリーフの概念 | 実装 | 備考 |
|---|---|---|
| `Project` | `projects.Project` | 状態、RAG 信号、進捗、PM/PMO |
| `Task` | `projects.WbsTask` | 優先度、次アクション、ボール保持者、PMO フォロー状態、関連タスク |
| `Risk` | `projects.Risk` | 確率 × 影響のスコア、対応方針、期限 |
| `Issue` | `projects.Issue` | 重大度、担当、期限、外部キー |
| `ChangeRequest` | `projects.ChangeRequest` | 影響範囲、影響タスク、工数、スケジュール影響、判断者・理由 |
| `Metric` / `QualityGate` | `projects.QualityMetric` | 閾値と向き（`higher_is_better`）で合否判定 |
| `AiProposal` | `dashboard.InterventionProposal` + `agents.AgentRun` | 入力・計画は AgentRun、提案と人の判断は InterventionProposal |
| `Report` / `Approval` | `pmo.Deliverable` + `pmo.Approval` | 版管理、AI 本文と確定本文の分離 |
| `KnowledgeDocument` | `documents.Document` | 台帳。旧 `index_map.json` 相当 |
| `Chunk` | `rag.Chunk` | 安定した `chunk_key` を持つ |
| `RetrievalResult` | `rag.RetrievalQuery` + `rag.RetrievedChunk` | スコア内訳を分けて保存 |
| `User` / `Role` | `accounts.User`（`role` フィールド） | |
| `AuditEvent` | `audit.OperationLog` | 保存時に秘密値をマスク |

## 関連図

```text
Tenant ─┬─ User ─── ProjectMember ─┐
        │                          │
        ├─ Project ────────────────┴─┬─ WbsTask ──┬─ (self) parent/children
        │                            │            └─ ChangeRequest.affected_tasks
        │                            ├─ Issue
        │                            ├─ Risk
        │                            ├─ ChangeRequest
        │                            ├─ Defect
        │                            ├─ QualityMetric
        │                            ├─ Milestone
        │                            ├─ HealthSnapshot
        │                            ├─ Alert ─── InterventionProposal
        │                            ├─ KpiMeasurement
        │                            ├─ Deliverable ─── Approval
        │                            ├─ PlanDraft
        │                            └─ Consultation
        │
        ├─ Document ─── DocumentPage
        │       └─ IngestJob
        │
        ├─ Template ─── TemplateOutput
        │
        ├─ VectorIndex ─── Chunk ──┬─ RetrievedChunk ─── RetrievalQuery ─── RagAnswer
        │                          └─ AnswerCitation ────────────────────────┘
        │
        ├─ ChatSession ─── ChatMessage
        │
        ├─ AgentRun ─┬─ AgentStep
        │            ├─ EvidenceEvaluation (1:1)
        │            └─ HumanReview
        │
        ├─ OperationLog
        └─ Feedback
```

## 設計判断

### なぜ主キーが UUID か

案件 ID や文書 ID が URL に出る。連番だと、他テナントのデータ件数や
存在有無が推測できてしまう。

### なぜ論理削除か

旧実装の方針「削除は物理削除せず、RAG 対象外として扱う」を引き継いでいる。
`SoftDeleteModel` を継承したモデルは `deleted_at` を持ち、`objects.alive()` で除外する。
削除済み文書のチャンクは検索に出ない（`active_chunks()` で二重に絞り込む）。

### なぜ AI 生成本文と確定本文を分けるか

`Deliverable.ai_generated_body` と `Deliverable.body` は別カラム。
分けておかないと、赤字率（人がどれだけ直したか）が測れない。
PoC の受け入れ条件「赤字率 20% 未満」の実測値は `Deliverable.correction_rate` で算出する。

### なぜアラートの検知日時と確認日時を分けるか

`Alert.detected_at` と `Alert.acknowledged_at` の差が、予兆検知の先行性そのもの。
「定例報告より N 営業日早く気づけたか」を後から検証するには、
検知した時点の記録が必要になる。

### なぜ検索結果のスコアを分けて持つか

`RetrievedChunk` はベクトル・語彙・リランクのスコアを別カラムで持つ。
統合スコアだけだと、なぜその順位になったかを説明できない。
検索精度を調整するときも、どちらの経路が効いているか切り分けられる。

### 案件横断のデータをどう扱うか

`Document.project` が null ならテナント共通ナレッジ（社内標準プロセス等）。
`VectorIndex` は `(tenant, project)` で一意で、project が null のものが共通インデックス。
ベクトル実体もインデックス単位で別ファイルに分かれるため、
案件をまたいだ誤参照はクエリのバグでは起きない。
