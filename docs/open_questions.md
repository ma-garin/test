# 未確定事項

実装を進める前に決めたいこと。決まったら該当行を消し、ADR か該当ドキュメントへ反映する。

## 1. 本番のデータストア

SQLite で始めているが、本番構成が未定。

- PostgreSQL へ移すなら、語彙検索を全文検索へ寄せられる（`LexicalIndex` が不要になる）
- ベクトルも pgvector に載せれば、ストアを 1 つに統合できる
- 一方、ローカル PC 完結の運用を続けるなら SQLite + JSONL のままでよい

**影響**: `apps/rag/services/lexical.py`, `vector_store.py`, `requirements/prod.txt`

## 2. Embedding プロバイダの既定

現在の既定は `local_hash`（API キー不要、意味検索はできない）。

- 実運用で OpenAI を使うなら、コストとデータ持ち出しの可否を確認する必要がある
- Ollama なら外部へ出さずに済むが、実行環境に GPU/メモリが要る
- プロバイダを変えるとインデックスの全再構築が必要

**影響**: `.env`, `apps/rag/services/embeddings.py`, 運用手順

## 3. 回答生成（LLM 呼び出し）の設計

検索と根拠評価までは実装したが、回答本文の生成が未実装。

- 回答フォーマットは仕様書 REQ-AG-007 の 7 セクション構成（`RagAnswer` のカラムは用意済み）
- LLM が使えない環境で、検索結果の要約だけを返すフォールバックを用意するか
- 引用（`AnswerCitation`）を LLM に出力させるか、事後に対応付けるか

**影響**: `apps/agents/services/tools.py`, `apps/rag/models.py`

## 4. 外部ツール連携（Jira / Slack / Confluence / Git）

旧棚卸しでは「未実装」。ブリーフでも必須になっていない。

- `Issue.external_key` は文字列として持っているだけで、同期はしない
- 読み取り連携を入れるなら、同期ジョブと最終同期時刻の表示が必要

**影響**: 新規アプリ（`apps/integrations/`）

## 5. 非同期ジョブ基盤

`IngestJob` は状態を持つが、実行は同期。

- Celery + Redis か、Django 標準の範囲で済ませるか
- 文書取込が数分かかる規模なら必須。数十秒なら後回しでよい

**影響**: `apps/documents/services/`, デプロイ構成

## 6. ファイル取込のウイルス対策

ブリーフ 8 章に「ファイル取込では形式、サイズ、ウイルス対策方針、保存先、削除方針を
定義する」とある。形式・サイズ・保存先・削除方針は実装したが、ウイルススキャンは未定。

- ClamAV 等をアップロードハンドラへ挟むか、ストレージ側（S3 + Lambda 等）で行うか

**影響**: `apps/documents/services/validation.py` の前段

## 7. 予兆検知の「先行性」の測り方

`Alert.detected_at` と `acknowledged_at` の差は取れるようにしたが、
「定例報告より N 営業日早い」の比較対象（定例報告の日付）をどこから取るかが未定。

- マイルストーンとして持つか、レポート（`Deliverable`）の作成日を使うか
- 営業日計算のカレンダー（祝日）をどう持つか

**影響**: `apps/dashboard/models.py`, KPI 集計

## 8. 旧データの移行

旧環境の `index_map.json`, `template_registry.json`, `07.feedback/*.jsonl` を
移行するかどうか。

- 履歴は業務情報を含む可能性があり、そのまま移すのは避けたい
- 評価用の Golden Dataset として使うなら、匿名化と承認が必要（旧ドキュメントにも記載あり）

**影響**: `docs/migration/from_streamlit.md`

## 9. ロールと権限の粒度

現在は `Role` の 7 種類で、承認権限とナビゲーション表示を制御している。

- 案件ごとにロールを変えたい要件があるか（現在は `ProjectMember.role_label` が自由文字列）
- 「参照のみ」ユーザーが RAG 検索を使えてよいか

**影響**: `apps/accounts/constants.py`, `apps/core/navigation.py`
