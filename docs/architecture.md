# アーキテクチャ

## 全体像

再構築ブリーフ 7 章の責務分割を、Django のモジュラーモノリスで実現している。
最初からマイクロサービスへ分けず、アプリ境界とサービス層で分離しておき、
必要になった単位で切り出せる状態にしておく方針。

```text
                    ┌─────────────────────────────┐
   ブラウザ ────────▶│ Web UI (Django Templates)    │
                    │  apps/*/views.py             │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │ 業務ロジック                  │
                    │  apps/*/services/            │
                    │  apps/*/selectors.py         │
                    └───┬──────────────────────┬──┘
                        │                      │
        ┌───────────────▼──────┐   ┌───────────▼──────────────┐
        │ 業務データ (RDB)      │   │ AI / RAG サービス          │
        │  apps/*/models.py    │   │  apps/rag/services/       │
        │                      │   │  apps/agents/services/    │
        └──────────────────────┘   └───────────┬──────────────┘
                                                │
                              ┌─────────────────▼─────────────────┐
                              │ ベクトルストア / 外部 LLM           │
                              │  var/vector_store/ · OpenAI/Ollama │
                              └───────────────────────────────────┘
```

## 責務の分け方

| レイヤ | 置き場所 | 責務 | やらないこと |
|---|---|---|---|
| Web UI | `apps/*/views.py`, `templates/` | 表示、フォーム、状態遷移の起動 | 業務ルールの判定、ORM の複雑なクエリ |
| 参照 | `apps/*/selectors.py` | 一覧・検索クエリ、**テナント分離** | 書き込み |
| 業務ロジック | `apps/*/services/` | 検証、集計、外部呼び出し、永続化の手順 | HTTP・テンプレートへの依存 |
| 永続化 | `apps/*/models.py` | データ構造、不変条件、単純な導出値 | 外部 API 呼び出し |
| AI/RAG | `apps/rag/services/`, `apps/agents/services/` | 取込、索引、検索、意図分類、根拠評価 | 画面都合の整形 |

ビューから ORM を直接触ることは避け、参照は selectors、更新は services を通す。
テナント分離を各ビューに書くと必ずどこかで漏れるため、`projects_for()` のような
関数へ集約している。

## アプリ境界

| アプリ | 持つもの | 依存してよいアプリ |
|---|---|---|
| `core` | 抽象モデル、テナント解決、ナビゲーション定義、AI 設定のマスク | `accounts` |
| `accounts` | テナント、利用者、ロール | （なし） |
| `projects` | 案件と配下の管理データ | `accounts` |
| `documents` | 文書台帳、取込ジョブ、ひな型 | `accounts`, `projects`, `rag`（回答参照のみ） |
| `rag` | チャンク、検索、回答、チャット | `accounts`, `projects`, `documents` |
| `agents` | Agentic 実行トレース、意図分類、根拠評価 | `rag`, `core` |
| `pmo` | 相談、計画、成果物、承認 | `projects`, `agents` |
| `dashboard` | ヘルススコア、アラート、介入提案、KPI | `projects`, `agents` |
| `audit` | 操作ログ、フィードバック | `accounts`, `projects`, `rag`, `agents` |

依存は上から下への一方向。`accounts` は他アプリを import しない。

## 差し替え可能にしてある箇所

移行の途中で技術選定が変わることを前提に、以下は抽象を挟んでいる。

| 箇所 | 抽象 | 既定 | 移行先の候補 |
|---|---|---|---|
| Embedding | `BaseEmbedder` | `LocalHashEmbedder` | OpenAI, Ollama |
| ベクトル保存 | `BaseVectorStore` | `JsonlVectorStore` | FAISS, pgvector |
| 語彙検索 | `LexicalIndex` | メモリ上の転置索引 | PostgreSQL 全文検索 |
| ツール実行 | `ToolRegistry` | ルールベース | LLM ツール呼び出し |
| DB | `DATABASE_URL` | SQLite | PostgreSQL |

`JsonlVectorStore` はチャンク数が数万を超えると線形走査が重くなる。
そこが最初のボトルネックになる想定で、`iter_vectors()` の実装を差し替えれば
呼び出し側は変更不要にしてある。

## 非同期ジョブ

文書取込・再インデックス・レポート生成は本来ワーカーへ出すべき処理。
現時点は同期実行だが、`IngestJob` が状態（queued / running / succeeded / failed）を
持っているので、Celery や RQ を導入したときにモデル変更なしで移せる。

## 認証・認可

- 認証: Django 標準のセッション認証
- テナント: `CurrentTenantMiddleware` が `request.tenant` を解決する
  - セッションで選択されたテナント → 所属テナント → None の順
  - 自分の所属外テナントは、セッションに入っていても採用しない（スーパーユーザーを除く）
- ロール: `apps/accounts/constants.Role`。ナビゲーションの表示制御と承認権限に使う

案件単位のアクセス制御は `ProjectMember` によるメンバーシップ。
テナント管理者は自テナント全案件を参照できる。
