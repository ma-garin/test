# VeriRAG PMO Agent (Django)

PMO 業務を支援する、根拠追跡可能な AI アシスタント。
Python/Streamlit の単一アプリだった現行システムを、Django のモジュラーモノリスとして再設計したもの。

現行実装の移植ではなく再設計です。UI 体験、業務機能、根拠に基づく AI 支援、
人による確認可能性を維持したうえで、UI・API・ジョブ・検索基盤・永続化を分離しています。

## できること

- **管制ダッシュボード** — 案件の状態、課題・リスク件数、重要アラートを一覧する
- **案件管理** — WBS、課題、リスク、変更要求、不具合、品質指標を案件単位で関連付ける
- **RAG 検索** — 登録文書をハイブリッド検索し、引用元とスコアの内訳を表示する
- **PMO 相談** — 相談内容を意図分類し、確認観点・根拠・根拠の十分性評価を返す
- **Agentic トレース** — AI の意図分類・実行計画・使用ツール・根拠評価を後から追跡する
- **監査** — 操作ログとフィードバックを、秘密値をマスクしたうえで保存する

## クイックスタート

```bash
make setup      # 仮想環境 + 依存 + .env
make migrate
make seed       # 体験用データ（利用者 pmo / パスワード demo-password）
make run        # http://127.0.0.1:8000/
```

API キーは不要です。既定の `AI_PROVIDER=local_hash` は、外部 API を呼ばずに検索経路を
最後まで通すための決定的な Embedding 実装です。

OpenAI / Ollama を使うときは、**設定画面（`/core/settings/`）から利用者ごとに設定できます**。
ロールに関係なく、全員が自分の API キーを持てます。設定は次の順に効きます。

1. 個人設定 … 自分の API キー・モデル
2. テナント既定 … 管理者が決める全員ぶんの既定
3. 環境変数（`.env`）… 最後の拠り所

上の段で空欄にした項目は、そのまま下の段に委ねます。キーは保存時に暗号化し、
画面にもログにもマスク済みの値しか出しません。保存できても有効なキーとは限らないので、
設定画面の「接続を確認する」で先に確かめてください。

Embedding モデルだけはテナント既定と環境変数からしか変えられません。利用者ごとに
変えられると、同じインデックスを別のベクトル空間で検索することになり、検索順位が
意味を失うためです。Embedding を変えたら `make index TENANT=demo` で作り直してください。

```bash
make test       # 単体・結合テスト（外部 API 呼び出しなし）
make lint       # ruff
make check      # システムチェック + マイグレーション漏れ検出
```

利用者視点のユースケーステスト（システムテスト）も回せます。

```bash
make systemtest   # ユースケース 735 件をロール別に実行する
make odc          # 不具合を ODC で分類する
```

進め方と考え方は `docs/systemtest/README.md` にあります。

自動実行の仕組みは置いていません。コミット前に手元で通してください。

## ディレクトリ構成

```text
.
├── config/                  Django プロジェクト設定
│   ├── settings/            base / local / test / production
│   └── urls.py              ルーティングの束ね口
├── apps/                    業務ドメインごとのアプリ
│   ├── core/                共通抽象モデル、テナント解決、ナビゲーション定義
│   ├── accounts/            テナント、利用者、ロール
│   ├── projects/            案件、WBS、課題、リスク、変更、不具合、品質指標
│   ├── documents/           文書台帳、取込ジョブ、Excel ひな型
│   ├── rag/                 チャンク、検索、回答、引用、チャット
│   ├── agents/              Agentic RAG（意図分類・計画・根拠評価・トレース）
│   ├── pmo/                 相談、計画策定、成果物、承認（HITL）
│   ├── dashboard/           ヘルススコア、アラート、介入提案、KPI
│   └── audit/               操作ログ、フィードバック、秘密値マスク
├── templates/               画面テンプレート
├── static/                  CSS
├── docs/                    設計資料と参照資料
│   ├── architecture.md      アーキテクチャ境界と責務分割
│   ├── domain_model.md      概念データモデルと実装テーブルの対応
│   ├── rag_flow.md          文書登録から回答までの流れ
│   ├── screen_map.md        画面一覧と移植状況
│   ├── api_contract.md      HTTP エンドポイント一覧
│   ├── requirements/        P0/P1 スコープと受入条件
│   ├── migration/           Streamlit 版からの移行方針
│   ├── adr/                 アーキテクチャ決定記録
│   ├── reference/           元の参照資料（現行コードの棚卸し、Agentic RAG 仕様）
│   └── screens/             UI モック、スクリーンショット
├── requirements/            base / ai / ingest / dev / prod
└── tests/                   アプリ横断のテスト置き場
```

各アプリの内部構成は共通です。

```text
apps/<name>/
├── models.py        永続化するもの
├── selectors.py     参照系のクエリ（テナント分離はここへ集約）
├── services/        業務ロジック（ビューからもコマンドからも呼ぶ）
├── views.py         画面
├── urls.py          パス定義
├── admin.py         Django 管理画面
├── migrations/
└── tests/
```

## 設計上の約束

再構築ブリーフの「守ること」を、コードで担保している箇所です。

| 約束 | 実装場所 |
|---|---|
| 認証情報を UI・ログへ出さない | `apps/core/services/secrets.py`（保存時暗号化）、`apps/core/services/ai_settings.py`（マスク）、`apps/audit/models.py`（保存時マスク） |
| 書き込み・承認は必ず認可を通す | `apps/accounts/services/permissions.py` の `require()`。各アプリの書き込みビューが冒頭で呼ぶ |
| AI 出力に根拠・信頼度・人の判断を持たせる | `apps/agents/models.py` の `EvidenceEvaluation` / `HumanReview` |
| 根拠不足なら断定せず承認へ進めない | `EvidenceEvaluation.blocks_approval`、`Deliverable.can_request_approval` |
| 案件・テナントの参照範囲を分離する | `apps/projects/selectors.py`、`apps/rag/services/vector_store.py`（インデックス単位でファイル分離） |
| 除外・削除文書を検索に出さない | `apps/rag/services/retriever.py` の `active_chunks()` |
| AI 未設定でも画面が壊れない | `get_embedder()` の local_hash 退避、`build_plan()` の LLM 必須ツール除外 |

## 現状と次にやること

実装済みは P0 の骨格（ダッシュボード、案件参照、文書台帳、RAG 検索、PMO 相談、トレース、監査）です。
ナビゲーションに「未移植」と付いた画面は、200 を返す未実装ページになっています。
移植状況は `/core/screen-map/` でも確認できます。

未着手の範囲と優先順位は `docs/requirements/p0_scope.md` と `docs/open_questions.md` を参照してください。
