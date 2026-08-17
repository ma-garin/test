# 未確定事項

実装を進める前に決めたいこと。決まったら「決まったこと」の表へ移し、
ADR か該当ドキュメントへ反映する。

**採番のきまり**: 各項目は `OQ-001` 形式の ID を持つ。IDは決定後も変えず、
他のドキュメントやコードからは見出しの位置（「3番」など）ではなく ID で参照する。
`apps/core/tests/test_open_question_ids.py` が参照先の実在を検査する。

## 決まったこと

| ID | 項目 | 決定 | 決定日 | 反映先 |
|---|---|---|---|---|
| OQ-001 | 本番のデータストア | **SQLite のまま**。PostgreSQL へは移さない | 2026-07-31 | `apps/rag/services/lexical.py` / `vector_store.py` を現状維持 |
| OQ-002 | Embedding プロバイダ | **`local_hash`（ダミー）のまま**。意味検索は行わない | 2026-07-31 | `.env.example` の既定値を維持。検索は語彙一致が主である前提で設計する |
| OQ-003 | 外部ツール連携 | 実装済み（Jira / Redmine / Slack / Teams / Confluence / Git） | 2026-07-31 | `apps/integrations/` |
| OQ-004 | 予兆検知の「先行性」の測り方 | 週次報告の作成日と比較。土日のみ除外し、祝日は考慮しない旨を画面へ明示 | 2026-07-31 | `apps/dashboard/services/poc_evaluation.py` |
| OQ-005 | 回答生成の設計 | **承認**。根拠アセンブラ（LLM不要）＋ 文体整形（LLM任意）の2層構成 | 2026-08-01 | `docs/adr/0004-answer-generation.md`（採用）。第1層 `apps/rag/services/answer.py` 実装済み。第2層はLLMプロバイダ決定後 |

OQ-002（Embedding をダミーのまま運用する）は OQ-005 の前提である。
検索精度が上がらない前提では、LLM に自由作文させるほど「裏の無い文」が増えるため、
ADR-0004 では事実の組み立てと文章の整形を分けている。

## OQ-006 非同期ジョブ基盤

`IngestJob` は状態を持つが、実行は同期。

- Celery + Redis か、Django 標準の範囲で済ませるか
- 文書取込が数分かかる規模なら必須。数十秒なら後回しでよい
- Confluence 取込は変更があったときにインデックスを張り直すため、
  ページ数が増えるとこの同期処理が長くなる

**影響**: `apps/documents/services/`, `apps/integrations/services/confluence_sync.py`, デプロイ構成

## OQ-007 ファイル取込のウイルス対策

ブリーフ 8 章に「ファイル取込では形式、サイズ、ウイルス対策方針、保存先、削除方針を
定義する」とある。形式・サイズ・保存先・削除方針は実装したが、ウイルススキャンは未定。

- ClamAV 等をアップロードハンドラへ挟むか、ストレージ側（S3 + Lambda 等）で行うか

**影響**: `apps/documents/services/validation.py` の前段

## OQ-008 旧データの移行

旧環境の `index_map.json`, `template_registry.json`, `07.feedback/*.jsonl` を
移行するかどうか。

- 履歴は業務情報を含む可能性があり、そのまま移すのは避けたい
- 評価用の Golden Dataset として使うなら、匿名化と承認が必要（旧ドキュメントにも記載あり）

**影響**: `docs/migration/from_streamlit.md`

## OQ-009 案件ロールの運用ルール

案件ロール（案件責任者 / 案件PMO / 担当 / 参照のみ）を実装した。
運用として決めたいのは次の 2 点。

- 案件メンバーの登録は誰が行うか（現在は Django admin のみ）
- 参照のみのメンバーに RAG 検索を許すか（現在は許している）

**影響**: `apps/projects/permissions.py`, 運用手順

## OQ-010 マイルストーンの登録手段

予実差分析は実装したが、登録手段は Django admin だけ。
PM が画面から登録できるようにするかを決めたい（要件表に登録機能の項目が無いため、
実装するなら要件の追加として扱う）。

**影響**: `apps/projects/`（フォームと画面の追加）
