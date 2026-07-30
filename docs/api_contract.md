# エンドポイント一覧

現時点は Django テンプレートによるサーバーサイドレンダリング。
SPA 化や外部連携が必要になった時点で、同じ services 層の上に DRF の
ViewSet を追加する（`REST_FRAMEWORK` の設定は `config/settings/base.py` に入れてある）。

## 画面エンドポイント

| メソッド | パス | URL 名 | 認証 | 内容 |
|---|---|---|---|---|
| GET | `/` | `dashboard:control` | 要 | 管制ダッシュボード |
| GET | `/healthz/` | `healthz` | 不要 | 死活監視。DB へ触れず `{"status":"ok"}` を返す |
| GET/POST | `/accounts/login/` | `accounts:login` | 不要 | ログイン |
| POST | `/accounts/logout/` | `accounts:logout` | 要 | ログアウト |
| GET/POST | `/accounts/tenant/` | `accounts:select_tenant` | 要 | テナント切替 |
| GET | `/core/settings/` | `core:settings` | 要（管理者） | AI 設定のマスク表示 |
| GET | `/core/screen-map/` | `core:screen_map` | 要 | 画面と移植状況の一覧 |
| GET | `/projects/` | `projects:list` | 要 | 案件一覧（参照可能なもののみ） |
| GET | `/projects/<uuid>/` | `projects:detail` | 要 | 案件詳細 |
| GET | `/documents/` | `documents:list` | 要 | 文書台帳 |
| GET | `/documents/templates/` | `documents:template_list` | 要 | ひな型一覧 |
| GET | `/rag/search/?q=` | `rag:search` | 要 | RAG 検索 |
| GET | `/rag/chat/` | `rag:chat` | 要 | チャット（履歴参照のみ） |
| GET | `/pmo/consultation/?q=` | `pmo:consultation` | 要 | PMO 相談 |
| GET | `/agents/` | `agents:run_list` | 要 | Agentic 実行一覧 |
| GET | `/agents/<uuid>/` | `agents:run_detail` | 要 | 実行トレース詳細 |
| GET | `/audit/operations/` | `audit:operation_list` | 要 | 操作ログ |
| GET | `/audit/feedback/` | `audit:feedback_list` | 要 | フィードバック |
| — | `/django-admin/` | — | 要（staff） | Django 管理画面 |

未実装画面（`docs/screen_map.md` で「未」のもの）も URL は存在し、
「未実装」と明示した 200 を返す。404 にしないのは、移植の進捗と不具合を
区別できるようにするため。

## 応答の約束

| 状況 | HTTP | 画面 |
|---|---|---|
| 未認証で要認証ページへアクセス | 302 → `accounts:login` | ログイン画面 |
| 参照権限のない案件 | 404 | （存在を秘匿するため 403 にしない） |
| インデックス未構築で検索 | 200 | 再構築コマンドの案内 |
| 検索結果ゼロ | 200 | 「該当する登録文書が見つかりません」 |
| 未実装画面 | 200 | 「この画面はまだ移植されていません」 |

## API 化するときの方針

1. ロジックは既に `services/` と `selectors/` にあるので、そこを再利用する
2. 認証はセッションのまま始め、外部連携が必要になったらトークン認証を足す
3. テナント分離は `request.tenant` に依存しているため、API でも
   `CurrentTenantMiddleware` を通す（または同等のスコープ解決を入れる）
4. ページングは `PageNumberPagination`（既定 25 件）

想定するリソース設計:

```text
GET    /api/v1/projects/
GET    /api/v1/projects/{id}/
GET    /api/v1/projects/{id}/tasks/
POST   /api/v1/projects/{id}/tasks/
PATCH  /api/v1/tasks/{id}/
GET    /api/v1/documents/
POST   /api/v1/documents/            # multipart。validate_upload() を通す
POST   /api/v1/search/               # {question, top_k} → 検索結果 + スコア内訳
POST   /api/v1/consultations/        # {question} → 意図・計画・根拠・評価
GET    /api/v1/agent-runs/{id}/      # トレース
POST   /api/v1/deliverables/{id}/approvals/
```
