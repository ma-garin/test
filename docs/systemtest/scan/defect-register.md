# 不具合台帳（静的スキャン由来）

ユースケーステスト（システムテスト）の準備として、全ディレクトリを走査して洗い出した
不具合の一覧。動的テストで再現・確認したうえで ODC 分類にかける。

- **ID**: `S-<領域>-<連番>`（S = Static scan）
- **重大度**: 高 = 業務が止まる / データが壊れる / 権限が破れる、中 = 誤った判断を誘発、低 = 使い勝手
- **状態**: 未着手 / 修整済 / 再実行待ち / 確認済

---

## dashboard（AIプロジェクト管制）

| ID | 重大度 | 箇所 | 内容 |
|---|---|---|---|
| S-DSH-01 | 高 | `apps/dashboard/views.py:97-116` | `detection_run`(POST) に権限チェックが無い。閲覧専用ロールが Alert / InterventionProposal を書き込める |
| S-DSH-02 | 高 | `apps/dashboard/views.py:326-374` | `intervention_decide` に承認権限チェックが無い。viewer が AI 提案の採用/不採用を確定でき、`decided_by` に記録される |
| S-DSH-03 | 中 | `apps/dashboard/views.py:327,347` | `intervention_decide` に HTTP メソッド制限が無い |
| S-DSH-04 | 中 | `apps/dashboard/views.py:313` | PoC 画面の事実誤認母数だけテナント単位。案件選択中でも他案件のフィードバックが混入し、他条件と母数が食い違う |
| S-DSH-05 | 中 | `apps/dashboard/services/overview.py:216-225` | 文書索引率がテナント全体になり案件絞り込みを無視。216-218 行の代入は 221-225 行で必ず上書きされるデッドコード |
| S-DSH-06 | 中 | `apps/dashboard/views.py:105` | `detection_run` が案件未選択時に参照可能な全案件へ一括書き込み。対象表示も確認も無い |
| S-DSH-07 | 高 | `apps/dashboard/services/detection/runner.py:129-135` | docstring は「1つ落ちても他を止めない」と書くが try/except が無い。検知器1つの例外で検知画面(GET)が 500 |
| S-DSH-08 | 中 | `apps/dashboard/services/ops_rules.py:271-278` | `task.updated_at` の None ガードが無く TypeError → 入力標準ルール画面が 500 |
| S-DSH-09 | 中 | `apps/dashboard/services/poc_evaluation.py:137-156` | `business_days_between` が1日ずつループ。異常な日付でリクエストが事実上ハング |
| S-DSH-10 | 中 | `apps/dashboard/services/overview.py:105-122` | 案件0件でヘルススコア0＝「要対応」表示。データ無しと危険の混同 |
| S-DSH-11 | 中 | `apps/dashboard/services/ops_rules.py:182-191` | 有効ルール0件でも「違反なし・遵守率100%」と表示。判定不能を表現できていない |
| S-DSH-12 | 中 | `apps/dashboard/services/quality.py:150-158` | 消化率が実質無変換（0〜1 の指標は 0% 表示）。複数案件時は最初の1案件の値を全体値として出す |
| S-DSH-13 | 高 | `templates/pages/task_gantt.html` | ガント表示にページャが無く 51 件目以降へ到達できない。件数表示も KPI と矛盾 |
| S-DSH-14 | 高 | `apps/dashboard/services/progress.py:19,110-111` | 進捗画面の KPI が `MAX_TASKS=20` で頭打ち。管制ダッシュボードの全件カウントと数字が食い違う |
| S-DSH-15 | 中 | `apps/dashboard/services/milestones.py:22,163-166` | マイルストーンも 30 件で打ち切った後に件数表示。31 件目以降の遅延が数から消える |
| S-DSH-16 | 中 | quality/ops_rules/detection/poc/progress | ページング無しで全件を materialize。打ち切り表示も無い |
| S-DSH-17 | 高 | `apps/dashboard/services/overview.py:206-214` | 「重要度順」が文字列辞書順（critical→info→warning）。warning が info より下に落ちる |
| S-DSH-18 | 高 | `apps/dashboard/models.py:84,102-109` ほか | アラートを確認・解消する画面が無い。`lead_time_days` が常に None で KPI が実測できず、未対応アラートが恒久的にヘルススコアを下げ続け、重複排除で同じ対象が二度と検知されない |
| S-DSH-19 | 低 | `apps/dashboard/services/kpi.py:47-52` | 改善率が 0 方向切り捨て。-0.5% が 0 になり悪化が赤くならない |
| S-DSH-20 | 低 | `apps/dashboard/selectors.py:142-144` | リスク一覧の `due_date` の NULL 順序が DB 依存 |
| S-DSH-21 | 低 | `apps/dashboard/services/earned_value.py:177,216` ほか | N+1（案件ごとに WbsTask / Alert / Deliverable を1クエリ） |
| S-DSH-22 | 中 | `apps/dashboard/services/poc_evaluation.py:258-259` | PoC 画面が全 Deliverable に事実照合＋全文 diff を上限なしで実行 |
| S-DSH-23 | 低 | `apps/dashboard/services/gantt.py:159-173` | ガントの案件グループ順がページ移動のたびに変わる |
| S-DSH-24 | 低 | `apps/dashboard/views.py:139` | `?view=Gantt` など大小差異で表へフォールバックし、タブの選択状態と実表示がずれる |

## pmo / agents（PMO支援・Agenticトレース）

| ID | 重大度 | 箇所 | 内容 |
|---|---|---|---|
| S-PMO-01 | 高 | `apps/pmo/views.py:261-280` | 承認 POST に `Action.APPROVE` の検証が無い。viewer が `decision=approved` を直接 POST して承認できる |
| S-PMO-02 | 高 | `apps/pmo/services/approval.py:128-134` | 自己承認が可能（四眼原則なし）。申請者と承認者を比較していない |
| S-PMO-03 | 高 | `apps/pmo/views.py:166-198,201-230` | 成果物の生成・確定本文保存に EDIT 権限チェックが無い |
| S-PMO-04 | 高 | `apps/agents/views.py:13-19` | Agentic トレースがテナント単位のみ。非メンバーが他案件の相談内容・入力文・根拠を全件閲覧できる |
| S-PMO-05 | 中 | `apps/pmo/views.py:204-207,264-267` | 不正 UUID で 500（`get_object_or_404` は ValidationError を捕まえない）。documents 側は対処済みで作法が不統一 |
| S-PMO-06 | 中 | `apps/pmo/views.py:315-328` | 不正・他人の `?deliverable=` を黙って先頭行へ丸める。別成果物を編集する誤操作を誘発 |
| S-PMO-07 | 中 | `apps/pmo/services/deliverables.py:89-98` | 全件に事実照合を実行してからページング。N+1 で件数増に耐えない |
| S-PMO-08 | 高 | `apps/pmo/selectors.py:38-43` | 差し戻し後に再申請できない（承認画面の一覧が DRAFT/PENDING のみ）。REJECTED は画面から消えデッドロック |
| S-PMO-09 | 中 | `templates/pages/pmo_approvals.html:50-59` | 差し戻し理由の入力欄が無く、常に空文字で記録される |
| S-PMO-10 | 高 | `apps/pmo/views.py:209` | 承認依頼後も本文を差し替えられる（承認直前スワップ）。承認者が見た内容と承認内容の一致が保証されない |
| S-PMO-11 | 高 | `apps/pmo/services/approval.py:106-138` | 確定本文が空でも承認できる。人が1文字も直していない AI 生成物が確定情報になる |
| S-PMO-12 | 中 | `apps/pmo/services/approval.py:64-70` | 「根拠評価が未実施です」が到達不能。根拠評価の無い成果物が素通しで承認可 |
| S-PMO-13 | 中 | `apps/agents/services/evidence.py:67-75` | `has_conflict` が常に False。矛盾検出によるブロックが死んでいる |
| S-PMO-14 | 高 | `apps/pmo/services/generators/plan.py:156` / `fact_check.py:89` | 計画ドラフトが必ず「事実誤認あり」判定になり承認できない（案件名の正規表現が過剰一致） |
| S-PMO-15 | 高 | `apps/pmo/services/generators/plan.py:84` / `fact_check.py:90` | フッタの「WBS 10件」を WBS コードとして拾い不一致判定 |
| S-PMO-16 | 高 | `apps/pmo/services/generators/plan.py:176-181` / `fact_check.py:85` | マイルストーン行を「実績が未来日」と誤判定 |
| S-PMO-17 | 中 | `apps/pmo/services/fact_check.py:363` | 「根拠あり」判定が数字の部分一致。トレースのどこかに同じ数字があるだけで根拠ありになる |
| S-PMO-18 | 低 | `apps/pmo/services/fact_check.py:449-453` | 存在しないフィールド `rationale` を読む死んだコード（実体は `notes`） |
| S-PMO-19 | 中 | `apps/pmo/services/generators/__init__.py:167` | `AgentRun.plan` にリストを入れており、トレース画面の実行計画が無言で全項目空欄 |
| S-PMO-20 | 中 | `apps/agents/models.py:200-241` | `HumanReview` を生成するコードが無い。トレースの「人による確認」が常に空 |
| S-PMO-21 | 低 | `apps/pmo/models.py:51-100` | `Consultation` モデルが未使用。相談履歴の一覧も再表示導線も無い |
| S-PMO-22 | 中 | `apps/agents/services/orchestrator.py:67-75` | 計画に `rerank_results` が載るが実行されない。使ったように見える |
| S-PMO-23 | 高 | `apps/pmo/views.py:40-50` | GET が書き込みを行う。`?q=` 付き URL を開くたびに AgentRun 一式が新規作成される |
| S-PMO-24 | 中 | `templates/pages/pmo_prompt_library.html:36` | 「この内容で相談」が未完成のテンプレ本文で即実行し、根拠不足の run がゴミとして残る |
| S-PMO-25 | 中 | `apps/pmo/views.py:114-118,242-245` | 案件を選んでも成果物・承認一覧が絞り込まれない。生成フォームだけ絞られ食い違う |
| S-PMO-26 | 低 | `apps/agents/services/screen_context.py:69-151` | 画面文脈の導線が13定義中3画面にしか配線されていない |
| S-PMO-27 | 低 | `templates/pages/task_detail.html:9` | `subject` の組み立てが未エスケープ。`&` `#` を含む名前でパラメータが壊れる |
| S-PMO-28 | 低 | `apps/pmo/views.py:40` | テナント未選択時に警告だけで切替導線が無い |
| S-PMO-29 | 低 | `apps/pmo/services/deliverables.py:70-86` | 平均赤字率の分母が KPI カードの総数と乖離する説明が無い |

## rag / documents / integrations / audit

| ID | 重大度 | 箇所 | 内容 |
|---|---|---|---|
| S-RAG-01 | 高 | `apps/rag/services/evaluation/retrieval.py:70,77` | 業務データ由来チャンクは `document=None`。`hit.chunk.document.title` が AttributeError → RAG評価画面が 500。`document_id=None` が "None" として Precision@K の分母に混入 |
| S-RAG-02 | 高 | `apps/rag/services/evaluation/static_check.py:42-46,55` | 同上。静的チェックスイートが 500 |
| S-RAG-03 | 高 | `apps/rag/services/embeddings.py:156` / `retriever.py:118` | 次元不一致で `zip(strict=True)` が ValueError。openai(1536次元)で作ったインデックスのままキーを外すと検索・チャットが 500。`VectorIndex.is_stale` はどの画面からも参照されていない |
| S-RAG-04 | 低 | `apps/rag/services/retriever.py:211` | `search_and_record()` は未使用の死にコード。使えば `IndexError` |
| S-INT-01 | 高 | `apps/integrations/views.py:139-175` | `connection_check` / `connection_sync` に管理者チェックが無い。viewer が Issue を書き込み、外向き HTTP を誘発できる |
| S-DOC-01 | 高 | `apps/documents/views.py:209-228` | 文書アップロードに権限判定が無い。viewer が RAG 対象文書を登録できる |
| S-DOC-02 | 高 | `apps/documents/views.py:67-84` | 再抽出に権限判定が無く、テナント全体のインデックス再構築を連打できる（レート制限も無し） |
| S-RAG-05 | 中 | `apps/rag/views.py:137,173,212` | 評価実行・Golden登録に権限判定が無い。高コスト処理を誰でも同期実行できる |
| S-AUD-01 | 中 | `apps/audit/views.py:28,45` | 操作ログ・フィードバック集計が全ロールに開放。他利用者の操作内容が見える |
| S-XXX-01 | 高 | 横断 | `permissions.can()/require()` が projects 以外のアプリから一度も呼ばれていない。上記の権限漏れはすべてこの未接続に起因 |
| S-INT-02 | 中 | `apps/documents/selectors.py:28` ほか | スーパーユーザーがテナント未選択だと全テナントのデータが1画面に混在。画面に表示も無い |
| S-DOC-03 | 低 | `apps/documents/models.py:38-41` | DEBUG 時に MEDIA が認証なしで全公開。他テナントの文書が URL 直打ちで取れる |
| S-AUD-02 | 高 | `apps/audit/services/feedback_stats.py:98` | User の主キーは UUID なのに `isdigit()` で判定。**利用者絞り込みが常に無視される**。選択状態も保持されない |
| S-RAG-06 | 低 | `templates/pages/rag_search.html:45` | `primary_index` が None のとき「〜 / チャンク」と壊れた表示 |
| S-RAG-07 | 低 | `apps/rag/views.py:107,176` | テナント未確定時に無言リダイレクト。audit 側は messages.error を出しており不統一 |
| S-INT-03 | 低 | `apps/integrations/views.py:199-212` | パイプライン画面だけページングが無い |
| S-DOC-04 | 中 | `apps/documents/services/validation.py:62-68` | 検証がファイル名の拡張子のみ。マジックナンバー・content_type を見ない |
| S-DOC-05 | 中 | `apps/documents/services/validation.py:20-28` | `.txt` / `.md` が `EXTENSION_TO_FILE_TYPE` に無く、依存ゼロで通るはずの経路が UI から到達不能 |
| S-DOC-06 | 低 | `apps/documents/services/validation.py:41-49` | 大きいファイルで `chunks()` 後の `seek(0)` が実装依存 |
| S-DOC-07 | 低 | `apps/documents/models.py:207` | `Template.file` に検証が無く、`.xlsm` にマクロ有効 MIME を付けて配信する |
| S-AUD-03 | 高 | `apps/audit/models.py:19-24` | マスクパターンが OpenAI 形式のみ。Slack Webhook URL / Jira `ATATT` / GitHub `ghp_` / Slack `xox*` が平文で長期保存される |
| S-INT-04 | 低 | `apps/integrations/services/sync.py:119-124` | 1件ごとの失敗で例外本文ごと `SyncJob.detail` へ保存し画面表示。外側は型名のみで方針が不一致 |
| S-INT-05 | 低 | `apps/integrations/models.py:159` | `SyncJob.detail` にマスクが掛かっていない |
| S-RAG-08 | 中 | `apps/rag/views.py:130-133` | チャットで検索範囲を選んでも送信のたびに既定へ戻る |
| S-INT-06 | 中 | `apps/integrations/models.py:32-33` | Confluence / Git が接続として作れるのに同期する導線が無く、直接叩くと事実と異なるエラーが返る |
| S-DOC-08 | 中 | `templates/pages/document_list.html` | 文書台帳に原本ファイルへのリンクが無い。本番では登録した文書を取り出す手段が存在しない |
| S-DOC-09 | 低 | `apps/documents/views.py:135` | 案件選択中は Excel 出力先の案件が1件しか出ず、画面の説明文と矛盾。upload と選択肢の母集合が違う |
| S-DOC-10 | 低 | `templates/pages/template_export.html:49-65` | 条件を変えるたびにプレビューへ戻る。「案件なし出力」ができない |
| S-RAG-09 | 低 | `apps/rag/services/evaluation/runner.py:92` | 前回実行の絞り込みが `__lte`。差分が 0 固定になりうる |
| S-INT-07 | 低 | `apps/integrations/services/notify.py:391-394` | `send(trigger=...)` を基底クラスが受け取らない。コネクタを1つ増やすと TypeError |

## ビルド・開発体験

| ID | 重大度 | 箇所 | 内容 |
|---|---|---|---|
| S-BLD-01 | 中 | `Makefile` / `config/settings/base.py:93` | クリーンチェックアウト直後の `make migrate` が `unable to open database file` で失敗する（`var/` が作られない） |
| S-BLD-02 | 中 | リポジトリ全体 | クリーンチェックアウトで `make lint` が 44 件のエラーで失敗する |

## projects（案件・WBS・課題・リスク・変更・不具合）

| ID | 重大度 | 箇所 | 内容 |
|---|---|---|---|
| S-PRJ-01 | 高 | `apps/projects/views.py:203,208,298,303,309,327,351,393,418,448,480,489,552,583,616` | 15 のビューに `Action.EDIT` の検査が無い。案件役割 `viewer` のメンバーが直接 POST で作成・編集・クローズ・アーカイブできる |
| S-PRJ-02 | 高 | `apps/projects/views.py:218` / `services/change_requests.py:46` | `change_decide` がテナントロールの `can_approve` だけを見て案件役割を見ない。案件では viewer の人が他人の案件の変更要求を承認できる |
| S-PRJ-03 | 中 | `templates/pages/change_list.html:60` | `row.change.effort_days` は存在しないフィールド（実体は `estimated_effort_days`）。工数列が常に「—」 |
| S-PRJ-04 | 低 | `templates/pages/change_list.html:59` | `impact_scope`（JSON リスト）を素で出力し `['設計', '実装']` と Python 表記で表示 |
| S-PRJ-05 | 中 | `templates/pages/task_list.html:24,30` | 件数（絶対数）を `width:{{ board.overdue }}%` に流用。5 件なら 5%、100 件超で振り切れバーが意味を持たない |

## UI / UX / UIコンポーネント

| ID | 重大度 | 箇所 | 内容 |
|---|---|---|---|
| S-UI-01 | 中 | screen_map / select_tenant / agent_run_detail / pmo_education / pmo_prompt_library / rag_evaluation / ops_rules / document_upload / template_export | `{% empty %}` が無く、0 件でヘッダだけの空テーブルになる |
| S-UI-02 | 中 | `templates/pages/project_detail.html:33,52,74,94,97` | `slice:":10"` で無言に打ち切り、続きへの導線が無い |
| S-UI-03 | 中 | select_tenant / select_project / document_upload / rag_evaluation / intervention_list / pmo_approvals / rag_chat / pmo_consultation / template_export | フォームにエラー表示が無い。サーバで弾かれた理由が画面に出ない |
| S-UI-04 | 中 | フォーム全般 | エラー表示方式が3系統に分裂（総括のみ / フィールド直下 / 何も出さない） |
| S-UI-05 | 高 | `static/css/app.css:616` ほか | `outline: none` かつボタン・リンクに `:focus-visible` 指定が皆無。キーボード操作で現在位置を完全に見失う |
| S-UI-06 | 高 | `static/css/app.css:343-344,239-254` | 未実装メニューのラベルが白背景に白文字で不可視。`.sb-tag` も同様 |
| S-UI-07 | 中 | `templates/layouts/base.html:43-47` | セクション開閉ボタンに `aria-expanded` / `aria-controls` が無い |
| S-UI-08 | 中 | `templates/layouts/base.html:63-66` | 折りたたみボタンのアクセシブルネームが空になる |
| S-UI-09 | 中 | `templates/layouts/base.html:79-81` | メッセージ領域に `role="status"` / `aria-live` が無い。`message.tags == 'error'` は `extra_tags` 付与で一致しなくなる |
| S-UI-10 | 中 | `templates/layouts/base.html` | skip link が無く、24 項目のサイドバーを毎回タブで抜ける必要がある |
| S-UI-11 | 中 | `templates/partials/task_filters.html:13-37` ほか | 絞り込み入力に `<label>` が無く placeholder で代用 |
| S-UI-12 | 低 | `templates/pages/pmo_deliverables.html:48` ほか | 暗黙ラベルと明示ラベルが混在 |
| S-UI-13 | 中 | `templates/pages/task_gantt.html:66-79` | ガントの棒がフォーカス不可で、情報が `title` 属性のみ |
| S-UI-14 | 低 | `templates/pages/pmo_approvals.html:54` | `disabled` ボタンの `title` に理由を入れているが、フォーカスできず読めない |
| S-UI-15 | 中 | 全一覧テーブル | `<caption>` と `scope="col"` が無い |
| S-UI-16 | 中 | `static/css/app.css` | 8〜10px の文字が多用され、日本語の実用限界を割っている |
| S-UI-17 | 高 | `static/css/app.css:713-717` | ブレークポイントが 1180px の1つだけ。768px 以下・480px 以下の指定が皆無 |
| S-UI-18 | 高 | `static/css/app.css:64-70` | スマホでもサイドバー 252px 固定。幅による自動折りたたみが無い |
| S-UI-19 | 中 | `static/css/app.css:350,115,691,384,449` | `.ph` / `.hdr-chips` に `flex-wrap` が無く、`.dl` は 160px 固定で狭幅に破綻 |
| S-UI-20 | 中 | 空状態・テーブル・新規作成・戻る導線 | 同じ意味の UI に 2〜3 種類のクラスが混在し、画面ごとに見た目が変わる |
| S-UI-21 | 中 | `issue_list.html` / `defect_list.html` | 同じ台帳系なのに絞り込み UI が無い（タスク・リスク・変更・介入にはある） |
| S-UI-22 | 低 | `defect_list.html` / `issue_form.html` / `risk_form.html` | 「クローズ」操作の置き場所が一覧の行内と編集画面下部で不統一 |
| S-UI-23 | 中 | `templates/` 全体 | インラインスタイルが 145 箇所。`task_gantt.html:16-39` はテンプレートに 24 行の `<style>` 直書き |
| S-UI-24 | 低 | `templates/pages/document_upload.html:13` | `callout g` は未定義クラス。成功メッセージが情報色で出る |
| S-UI-25 | 低 | `integration_list.html:31,46` / `poc_evaluation.html:90` | レイアウトクラス `.sg`（grid）をテキスト装飾として誤用 |
| S-UI-26 | 低 | `templates/pages/risk_form.html:38-41` | `.dl` の中身が `dt`/`dd` でなく `div` でスタイルが当たらない |
| S-UI-27 | 低 | `static/css/app.css:189,223-233` | `.sb-badge` の二重定義でコメントの意図が効いていない |
| S-UI-28 | 中 | `templates/layouts/auth.html` | メッセージブロックが無く、ログイン画面でフラッシュメッセージが出ない |
| S-UI-29 | 中 | `static/css/app.css` | 間隔・タイポ・z-index のトークンが未定義。角丸・影もトークンが守られていない |
| S-UI-30 | 中 | `static/css/app.css` | ダークモード非対応。`prefers-color-scheme` / `[data-theme]` / `color-scheme` の宣言が皆無で、ハードコード色が約30箇所 |

## accounts / core / config（追加スキャン）

| ID | 重大度 | 箇所 | 内容 |
|---|---|---|---|
| S-ACC-01 | 高 | `apps/accounts/backends.py:20-65` | パスワード検証なしで、未登録アドレスは利用者を自動作成。しかも既定ロールが `pmo`（承認権限つき）で、最初のテナントの実データに第三者が入れる |
| S-ACC-02 | 高 | `config/settings/production.py` | 本番設定が `EmailOnlyBackend` を外していない。docstring は「本番では使えない」と書いているのに防御が無い |
| S-CFG-01 | 高 | `manage.py` / `config/wsgi.py` / `config/asgi.py` | 既定の設定モジュールが `local`。設定を渡し忘れた本番起動が `DEBUG=True` / `ALLOWED_HOSTS=["*"]` / 既定 SECRET_KEY で立ち上がる |
| S-CFG-02 | 高 | `config/settings/base.py:25` | SECRET_KEY の既定値がリテラル。API キーの暗号鍵はここから導出するため、既定のままだと実質平文 |
| S-ACC-03 | 高 | `apps/accounts/services/permissions.py:152-160` | 越境チェックが `project` を持つ対象にしか効かない。案件に紐づかない対象（テナント共通の文書など）は他テナントでも通る |
| S-ACC-04 | 高 | `apps/accounts/services/permissions.py:159` | `user.tenant_id is None` だと越境チェックを丸ごと素通りする。無所属が最強の権限になる |
| S-ACC-05 | 中 | `apps/accounts/services/permissions.py:177` | 不正な案件役割で `ValueError` → 500。同ファイルの一覧側はガードしているのに判定本体だけ無防備 |
| S-ACC-06 | 中 | `apps/accounts/views.py:75,115` | テナント/案件の切替に UUID でない値を送ると `ValidationError` で 500 |
| S-COR-01 | 中 | `apps/core/views.py:126-132` | 管理権限ありでテナント未確定のとき、テナント既定の保存が NOT NULL 違反で 500 |
| S-COR-02 | 中 | `apps/core/pagination.py:29-36` | 並び順を強制しないため、ページをまたぐと行が重複・欠落しうる。10 以上のビューが共有 |
| S-COR-03 | 中 | `apps/core/services/ai_settings.py` | 接続確認が利用者指定の URL へ無検証で通信する（SSRF）。到達可否とエラー本文が画面に返る |
| S-COR-04 | 中 | `apps/core/middleware.py:62` | 既定経路が `is_active` を見ない。テナントを止めても利用停止にならない |
| S-COR-05 | 低 | `apps/core/middleware.py:53-62` | 選べないテナントIDがセッションに残り続け、毎リクエスト引き直す |
| S-COR-06 | 中 | `apps/core/navigation.py:30-37` | 全項目の `roles` が空で可視性制御が事実上無効。参照のみのロールにも管理系メニューが並ぶ |
| S-COR-07 | 低 | `apps/core/navigation.py:31` | `roles` が空だと未認証にも True を返す |
| S-ACC-07 | 中 | `apps/accounts/models.py:71,75` | `can_approve` / `is_tenant_admin` が `ROLE_PERMISSIONS` と別系統で、表から権限を外しても減らない |
| S-ACC-08 | 中 | `apps/accounts/views.py:72-81` | テナント切替の失敗時にメッセージが出ず、押しても何も起きない画面になる |
| S-ACC-09 | 低 | `apps/accounts/views.py:81` | テナント切替後に `next` を無視する（案件切替とは挙動が違う） |
| S-ACC-10 | 中 | `templates/pages/login.html` | hidden の `next` が無く、`?next=` からのログイン後に元の画面へ戻れない |
| S-ACC-11 | 低 | `apps/accounts/backends.py:33,67-78` | メールの大小差異で入るアカウントが不定。ユーザー名の採番が非アトミックで連番が -2 から始まる |
| S-ACC-12 | 中 | `apps/accounts/views.py:18` | ログインに回数制限も監査ログも無い |
| S-CFG-03 | 中 | `config/settings/production.py` | セッション有効期限・DATABASES の上書き・ALLOWED_HOSTS の必須化が無く、未設定でも黙って動く |
| S-CFG-04 | 中 | `config/urls.py:29-33` | DEBUG 時に MEDIA と docs を無認証配信。本番は逆に media の配信経路が未設計 |
| S-COR-08 | 低 | `apps/core/views.py:33-34` | テナント未割当の利用者の設定変更が監査ログに残らない |
| S-COR-09 | 低 | `apps/core/middleware.py:36-43` | ストリーミング応答では本体の反復前に文脈が戻り、AI 設定の解決が匿名扱いになる |
