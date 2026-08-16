# PMO_AI 評価 Runbook

## 目的

PMO_AIエージェントを実装変更前後で安全に比較するため、APIを呼ばない静的評価と、将来追加するAPIあり評価の境界を明確にする。

## APIなし評価

実行場所:

```powershell
cd C:\VeriRAG_test
python eval/run_static_checks.py
```

確認内容:

- `04.faiss_index/chunks.json` が読み込めること。
- `04.faiss_index/lexical_index.json` の `chunk_count` が `chunks.json` の件数と一致すること。
- `eval/golden/rag_golden_v1.jsonl` が `eval/schema/golden_schema.json` に適合すること。
- Golden Datasetの `expected_sources` が `chunks.json`、`index_map.json`、`template_registry.json` の候補に存在すること。

禁止事項:

- `.streamlit/secrets.toml` を読まない。
- 既存履歴JSONLを読まない。
- OpenAI/Ollama APIを呼ばない。
- FAISS indexを再生成しない。
- 既存データを削除、移動、上書きしない。

## 評価タイプの違い

| 評価タイプ | API | 主な入力 | 判定対象 | 判定しないこと |
| --- | --- | --- | --- | --- |
| 静的チェック | なし | Golden Dataset, schema, chunks, lexical index, index map | ファイル存在、件数整合性、スキーマ、期待ソース存在 | 検索順位、回答品質、引用品質 |
| APIなし検索評価 | なし | Golden Dataset, chunks, lexical index | Top-K候補のsource coverage | 本番FAISS検索の完全再現、LLM回答品質 |
| APIあり評価 | あり | Golden Dataset, アプリ検索/生成結果 | 回答、引用、抑制、必須セクション | APIキーや履歴内容の出力 |

## 静的チェック

実行コマンド:

```powershell
cd C:\VeriRAG_test
python eval/run_static_checks.py
```

静的チェックは評価データと既存indexメタデータの前提確認である。`Result: PASS` は評価基盤の入力が壊れていないことを示すが、検索品質や回答品質を保証しない。

## APIなし検索評価

実行コマンド:

```powershell
cd C:\VeriRAG_test
python eval/run_offline_retrieval_eval.py
```

出力:

- `eval/results/offline_retrieval_YYYYMMDD_HHMMSS.jsonl`
- `eval/reports/offline_retrieval_sample.md`

確認内容:

- Golden Datasetの `question` と `expected_terms` から簡易検索語を作る。
- `04.faiss_index/lexical_index.json` の `postings` を使ってTop-K候補を計算する。
- lexical indexにない語は `chunks.json` 本文の短い照合で補助する。
- Top-Kは 3, 5, 10 を見る。
- `expected_sources` がTop-K候補のsourceに含まれるかをsource recallとして見る。

注意事項:

- APIなし検索評価は、本番検索品質の完全保証ではない。アプリ本体のFAISS検索、ハイブリッド検索、reranking、LLM回答生成とは別の軽量オフライン評価である。
- 初期段階ではsource coverageを主に見る。`expected_chunk_ids` が空の場合、chunk単位の合否は判定しない。
- `expected_sources` が空のG08は検索Recallの失敗扱いにしない。APIあり評価で幻覚抑制と根拠不足の明示を確認する。
- `template_registry.json` のようにchunks/lexical indexに含まれないソースは、検索評価では評価不能として警告する。
- `expected_chunk_ids` はPM/PMOの人手レビュー後にのみ更新する。

結果ファイル運用:

- 通常実行では固定sampleファイルを更新せず、タイムスタンプ付きJSONLを `eval/results/` に保存する。
- `eval/results/offline_retrieval_sample.jsonl` は過去サンプルとして残す。
- ファイル出力なしで標準出力サマリだけ確認する場合は `python eval/run_offline_retrieval_eval.py --no-write` を使う。
- 出力先を明示したい場合は `python eval/run_offline_retrieval_eval.py --output eval/results/offline_retrieval_YYYYMMDD_HHMMSS.jsonl` を使う。
- `eval/results/` に書けない環境では、`--output-dir` で書き込み可能な退避先を指定する。
- `--output-dir` の例: `python eval/run_offline_retrieval_eval.py --output-dir C:\VeriRAG_test\eval_local_results`
- 一時領域へ保存する例: `python eval/run_offline_retrieval_eval.py --output-dir $env:TEMP\pmo_eval_results`
- 継続的に退避先を使う場合は、環境変数 `PMO_EVAL_OUTPUT_DIR` を設定する。
- 環境変数の例: `$env:PMO_EVAL_OUTPUT_DIR='C:\VeriRAG_test\eval_local_results'; python eval/run_offline_retrieval_eval.py`
- 出力先の優先順位は `--output`、`--output-dir`、`PMO_EVAL_OUTPUT_DIR`、`eval/results`、標準出力のみの順とする。
- すべての出力先に書けない場合も、評価サマリは `Output: not written` として表示し、評価自体は成功扱いにする。
- `--overwrite` は既存成果物を意図的に置き換える場合だけの例外操作であり、通常運用では使わない。

PermissionError時にやってはいけないこと:

- 管理者実行で突破しない。
- 対象ファイルを削除しない。
- ファイルやディレクトリの権限を変更しない。
- 既存成果物を上書きしない。

PermissionError時の安全な対応:

- 表示された対象パスを確認する。
- ファイルを開いているアプリがあれば閉じる。
- `--no-write` でサマリだけ確認する。
- 新しいタイムスタンプ付き `--output` パスを指定する。
- 書き込み可能な `--output-dir` または `PMO_EVAL_OUTPUT_DIR` を指定する。

Python構文確認:

- `python -m py_compile eval/run_offline_retrieval_eval.py` は `eval/__pycache__/` に書き込むため、権限がない環境では失敗する。
- `__pycache__` 権限で失敗する場合は、書き込みを伴わない代替として以下を使う。

```powershell
python -c "import ast; ast.parse(open('eval/run_offline_retrieval_eval.py', encoding='utf-8').read()); print('ast.parse: PASS')"
```

## expected_chunk_ids レビュー支援パック

実行コマンド:

```powershell
cd C:\VeriRAG_test
python eval/run_static_checks.py
python eval/run_offline_retrieval_eval.py
python eval/build_expected_chunk_review_pack.py
```

出力:

- `eval/review/expected_chunk_candidates_v1.jsonl`
- `eval/review/expected_chunk_review_sheet_v1.csv`
- `eval/reports/expected_chunk_review_pack.md`

目的:

- Golden Datasetの `expected_chunk_ids` をPM/PMOが安全に確定するための候補chunkを提示する。
- expected_sourceごとに候補chunk、page、score、matched_terms、最大200文字のsnippetを確認できるようにする。
- G10のようにTop10で漏れたexpected_sourceも、source別候補として確認できるようにする。

注意事項:

- レビュー支援パックは候補提示であり、Golden Dataset本体を更新しない。
- snippetはレビュー補助であり、チャンク本文全文は保存しない。
- `expected_chunk_ids` は、PM/PMOがsource、page、snippet、原資料の妥当性を確認した後にのみ更新する。
- `accept` と判断できない候補は `needs_discussion` または `reject` とし、Golden Datasetには反映しない。
- `template_registry.json` のようにchunks/lexical index対象外のソースは、別資料でレビューする。

## expected_chunk_ids レビュー高速化パック

実行コマンド:

```powershell
cd C:\VeriRAG_test
python eval/build_review_fast_view.py
```

出力:

- `eval/review/expected_chunk_review_fast_view.md`
- `eval/review/expected_chunk_review_working.csv`
- `eval/reports/expected_chunk_review_fast_view_summary.md`

使い方:

- `expected_chunk_review_fast_view.md` でケース別候補、判断ポイント、source_forced候補を確認する。
- 実際の編集は `expected_chunk_review_working.csv` で行う。
- 元の `expected_chunk_review_sheet_v1.csv` は上書きしない。
- `decision_hint` は判断補助であり、`accept` ではない。Golden反映対象にもならない。

## expected_chunk_ids 反映手順

レビューシート:

- `eval/review/expected_chunk_review_sheet_v1.csv`

`review_decision` の意味:

- `accept`: PM/PMOが根拠chunkとして採用すると判断した行。小文字 `accept` のみ反映対象。
- `reject`: 根拠として不適切な行。反映しない。
- `needs_discussion`: 判断保留。反映しない。
- `unreviewed`: 未確認。反映しない。
- 空欄: 未確認扱い。反映しない。

acceptを付けてよい条件:

- PM/PMOがsnippet、source、page、必要なら原資料を確認している。
- 質問に対する根拠chunkとして直接使える。
- expected_sourceとchunkのsourceが一致している。
- 他の候補より根拠として妥当であることを説明できる。

acceptを付けてはいけないケース:

- G08にはacceptを付けない。登録資料外情報の抑制回答をAPIあり評価で見る。
- G09の `template_registry.json` はchunkではないためaccept対象外。D06のchunk候補だけを確認する。
- G10は、既存G10を採用するかG10A〜G10Dへ分割するかを判断した後にacceptを付ける。
- `decision_hint=likely_relevant` だけを理由にacceptしない。

dry-run:

```powershell
cd C:\VeriRAG_test
python eval/apply_expected_chunk_review.py --dry-run
```

- Golden Datasetを変更しない。
- `eval/reports/expected_chunk_apply_dry_run.md` に反映候補、更新前後の予定、警告、エラーを出す。
- accept行がない場合はno-opで正常終了し、Golden Dataset未更新と明記する。

apply:

```powershell
cd C:\VeriRAG_test
python eval/apply_expected_chunk_review.py --apply
```

- `review_decision=accept` の行だけを `expected_chunk_ids` に反映する。
- apply前に `eval/backups/changeset_003bc/` へGolden Datasetのバックアップを作る。
- G08のように `expected_sources` が空のcaseには `expected_chunk_ids` を入れない。
- `template_registry.json` はchunkではないため、`expected_chunk_ids` に入れない。

apply前に確認すること:

- Codex判断で `review_decision` を `accept` に変更していないこと。
- `accept` はPM/PMOレビュー済み行だけであること。
- `python eval/apply_expected_chunk_review.py --dry-run` がPASSしていること。

apply後に実行する評価コマンド:

```powershell
python eval/run_static_checks.py
python eval/run_offline_retrieval_eval.py --output-dir C:\VeriRAG_test\eval_local_results
```

`expected_chunk_ids` が入っているcaseでは、APIなし検索評価にchunk recallが追加される。

## G10分割ドラフト

ドラフト:

- `eval/golden/rag_golden_v1_g10_split_draft.jsonl`
- `eval/reports/g10_split_proposal.md`

扱い:

- 既存 `eval/golden/rag_golden_v1.jsonl` へ自動統合しない。
- G10A〜G10Dは、週次PMO報告を進捗、リスク、課題、品質に分けて評価するための候補である。
- PM/PMOレビューで質問文、expected_sources、expected_terms、required_answer_sectionsを確認する。

既存Goldenへ統合してよい条件:

- PM/PMOがG10分割を正式採用すると判断している。
- 各ケースのexpected_sourcesが妥当である。
- APIなし検索評価でsource coverageを確認できる。
- 必要に応じてexpected_chunk_idsレビュー支援パックを再生成し、人手レビューでchunk根拠を確定できる。
- 既存G10を残す、置き換える、併用する方針が明確である。

## APIあり評価

APIあり評価は次フェーズ以降に追加する。対象は検索結果、生成回答、引用、抑制挙動であり、実行前にAPIキー、モデル、費用、保存先、入力データの扱いを明示する。

APIあり評価で許可する予定の範囲:

- Golden Datasetの質問を使った検索Top-Kの取得。
- 生成回答の必須セクション有無の検査。
- 引用元が期待ソースまたは許容ソースに含まれるかの検査。
- 根拠不足ケースで「確認できない」と明示できるかの検査。

APIあり評価でも禁止する範囲:

- secrets値の出力。
- 既存履歴JSONLへの追記または読み込み。
- FAISS indexの再生成。
- Golden Datasetに根拠未確認の `expected_chunk_ids` を追加すること。

## Golden Dataset更新ルール

- IDは `G01` 形式で固定し、既存IDの意味を変更しない。
- `expected_sources` は登録済み資料名、または `template_registry.json` のような明示的なローカル根拠に限定する。
- `expected_chunk_ids` は検索結果を人手で確認し、該当チャンクの根拠性を確認した後にだけ追加する。
- `allowed_general_knowledge=false` のケースでは、登録資料外の知識で断定しないことを評価する。
- 変更理由は `notes` に短く残す。
- Golden Dataset変更後は必ず `python eval/run_static_checks.py` を実行する。
- 検索評価に関係する変更後は `python eval/run_offline_retrieval_eval.py` も実行する。

## working CSVから正式レビューシートへの反映

ChangeSet-003Dで作成した `eval/review/expected_chunk_review_working.csv` は、PM/PMOがレビュー判断を書き込む作業用ファイルである。作業用ファイルの判断を正式レビューシート `eval/review/expected_chunk_review_sheet_v1.csv` へ反映する場合は、同期ツールを使う。

```powershell
cd C:\VeriRAG_test
python eval/sync_review_working_to_sheet.py --dry-run
```

dry-runでは正式レビューシートを更新しない。以下を確認する。

- `review_decision` 別件数がPM/PMOのレビュー結果と一致していること。
- case別の `accept` / `reject` / `needs_discussion` / `unreviewed` 件数が妥当であること。
- `accept行一覧` と `needs_discussion行一覧` に意図しない行がないこと。
- バリデーション結果が `PASS` であること。
- G08に `accept` がないこと。
- G09の `template_registry.json` 相当の非chunk候補に `accept` がないこと。
- G10は既存G10を残すか、G10A〜G10Dへ分割するかを判断した後にレビュー判断を確定すること。
- dry-runレポートに表示されるバックアップ予定先が、対象ChangeSetのディレクトリになっていること。

バックアップ先はChangeSet単位で明示する。通常は `--changeset-id` を使う。

```powershell
cd C:\VeriRAG_test
python eval/sync_review_working_to_sheet.py --dry-run --changeset-id changeset_003f_retry2
python eval/sync_review_working_to_sheet.py --apply --changeset-id changeset_003f_retry2
```

`--changeset-id changeset_003f_retry2` を指定した場合、バックアップ予定先は `eval/backups/changeset_003f_retry2/expected_chunk_review_sheet_v1.csv` になる。

任意のバックアップディレクトリを明示したい場合だけ `--backup-dir` を使う。`--backup-dir` と `--changeset-id` を同時指定した場合は `--backup-dir` が優先される。

```powershell
python eval/sync_review_working_to_sheet.py --dry-run --backup-dir C:\VeriRAG_test\eval\backups\manual_review_sync
```

`--overwrite-backup` は既存バックアップを上書きするため、通常運用では使わない。バックアップ先が既に存在する場合は、別の `--changeset-id` を使うか、既存バックアップの扱いを人が確認してから判断する。

applyしてよい条件は、dry-runレポート `eval/reports/review_sync_dry_run.md` をPM/PMOが確認し、正式レビューシートへ反映してよいと判断した場合に限る。

```powershell
cd C:\VeriRAG_test
python eval/sync_review_working_to_sheet.py --apply --changeset-id changeset_003f_retry2
```

apply時は正式レビューシートの更新前バックアップを、dry-runレポートに表示されたバックアップ予定先へ作成する。同期対象は `review_decision`、`review_notes`、`reviewer`、`reviewed_at` の4列だけである。`decision_hint` は判断補助であり、正式レビューシートへ反映しない。

apply後に実行するコマンド:

```powershell
python eval/run_static_checks.py
python eval/run_offline_retrieval_eval.py --output-dir C:\VeriRAG_test\eval_local_results
python eval/apply_expected_chunk_review.py --dry-run
```

重要: この同期はレビューシートの反映だけであり、Golden Datasetはまだ更新されない。`expected_chunk_ids` のGolden反映は次ChangeSetで `eval/apply_expected_chunk_review.py --dry-run` を確認してから行う。

戻し方:

- dry-runでは正式レビューシートを更新していないため、戻し作業は不要。
- apply後に戻す場合は、dry-run/applyレポートに表示されたバックアップファイルを `eval/review/expected_chunk_review_sheet_v1.csv` へ戻す。
- Golden Datasetはこの工程では変更しないため、Golden Datasetの戻しは不要。

## 評価結果保存ルール

- 静的チェックの代表例は `eval/reports/` にMarkdownで保存する。
- APIなし検索評価の過去サンプルは `eval/results/offline_retrieval_sample.jsonl` と `eval/reports/offline_retrieval_sample.md` に保存する。
- 通常のAPIなし検索評価結果は `eval/results/offline_retrieval_YYYYMMDD_HHMMSS.jsonl` 形式で保存する。
- 将来の機械実行結果は `eval/results/` に日付またはタイムスタンプ付きファイルで保存する。
- 履歴JSONLやチャット内容を評価結果に混在させない。
- APIあり評価の出力には秘密情報、APIキー、個人情報、未承認の業務履歴を含めない。

## expected_chunk_ids反映後の検索漏れ分析

Golden Datasetへ `expected_chunk_ids` を反映した後、検索ロジックを変更する前に必ずAPIなし検索評価と検索漏れ分析を実行する。

```powershell
cd C:\VeriRAG_test
python eval/run_static_checks.py
python eval/run_offline_retrieval_eval.py --output-dir C:\VeriRAG_test\eval_local_results
python eval/analyze_retrieval_gaps.py
```

`eval/analyze_retrieval_gaps.py` は以下を入力にする。

- `eval/golden/rag_golden_v1.jsonl`
- `04.faiss_index/chunks.json`
- `04.faiss_index/lexical_index.json`
- 最新の `eval_local_results/offline_retrieval_*.jsonl`

出力:

- `eval/reports/retrieval_gap_analysis.md`
- `eval/reports/retrieval_improvement_candidates.md`

`retrieval_gap_analysis.md` の読み方:

- 全体サマリでMean chunk recall@3/@5/@10とmissing caseを確認する。
- case別の漏れ状況で、source recallではなくchunk recallが低いcaseを確認する。
- missing chunk一覧では、`missing_reason_candidate` を原因候補として扱う。断定ではない。
- snippetは最大200文字の確認用抜粋であり、チャンク本文全文ではない。
- G10のような横断ケースは、検索改善と評価設計を分けて判断する。

chunk recallが低い場合の判断手順:

1. G10のような横断ケースか、単一管理領域のcaseかを分ける。
2. source自体がTop10にないのか、source内のexpected chunkだけが漏れているのかを分ける。
3. `source_competition` はsource内Top-K評価やsource_forced_candidates方式で追加確認する。
4. `query_terms_mismatch` はexpected_termsの補強候補としてPM/PMOレビューする。
5. `g10_cross_case_noise` は検索ロジック変更より先にG10A〜G10D分割を検討する。

検索ロジックを変更する前に確認すること:

- Goldenの期待値設計が粗すぎないこと。
- expected_chunk_idsが必須根拠と補助根拠に混在していないこと。
- source recallが維持されていること。
- 変更前後のTop-K差分レポートを保存できること。
- APIあり評価へ進む前に、APIなし評価で改善仮説を説明できること。

## source_forced_candidates評価

source_forced_candidates評価は、通常Top-Kとは別に、Golden caseの `expected_sources` ごとに候補chunkをsource内へ限定して再スコアリングするAPIなし評価である。通常Top-Kで漏れた `expected_chunk_ids` が、期待source内では上位に出るかを確認するために使う。

通常Top-K評価との違い:

- 通常Top-Kは全chunkを同じ候補集合として扱う。
- source_forcedは `expected_sources` に含まれるchunkだけをsource別に評価する。
- source_forcedは本番検索品質の完全保証ではなく、原因切り分け用の補助指標である。
- `template_registry.json` のような非chunkソースはsource_forced評価対象外にする。
- G08のように `expected_sources` が空のcaseはsource_forced評価対象外にする。

実行手順:

```powershell
cd C:\VeriRAG_test
python eval/run_static_checks.py
python eval/run_offline_retrieval_eval.py --output-dir C:\VeriRAG_test\eval_local_results
python eval/analyze_retrieval_gaps.py
```

確認するレポート:

- `eval/reports/source_forced_eval_sample.md`
- `eval/reports/retrieval_gap_analysis.md`
- `eval/reports/retrieval_improvement_candidates.md`

source_forcedで拾える場合の解釈:

- 期待source内では該当chunkを上位化できているため、expected_chunk自体の妥当性は比較的高い。
- 通常Top-Kでのsource間競合、横断質問の設計、query/source配分、Top-K幅が主な原因候補になる。
- G10のような横断ケースでは、検索ロジック改善より先にG10A〜G10D分割を検討する。

source_forcedでも拾えない場合の解釈:

- expected_termsが該当chunkの用語と噛み合っていない可能性がある。
- スコアリング式や語彙展開の不足を疑う。
- expected_chunk_idsが必須根拠として妥当かをPM/PMOレビューへ戻す。

G10のような横断ケースでの使い方:

- 通常Top-Kとsource_forcedの差を見る。
- source_forcedで回収できるが通常Top-Kで漏れる場合は、複数sourceを1問で評価していることによる競合を疑う。
- G10分割とneeds_discussion追加レビューは後続改善として扱い、source_forced結果を判断材料にする。
- 実装改善前に、通常Top-Kとsource_forcedの比較結果をChangeSetのレポートへ残す。

## APIあり回答評価

APIあり回答評価は、検索結果そのものではなく、生成回答の最低限の品質を確認するために使う。対象は、必須セクションの有無、期待sourceへの言及、expected_chunk_idsへの言及、根拠不足時の抑制表現、G08のような登録資料外質問での幻覚抑制である。

APIなし評価との違い:

- APIなし評価は `chunks.json` と `lexical_index.json` を使い、検索候補の妥当性を見る。
- APIあり評価は取得済みTop-K chunkをpromptに入れ、生成回答の構造と根拠表現を見る。
- APIあり評価は費用と外部API呼び出しを伴うため、必ず `--dry-run` で計画を確認してから `--run` する。
- APIあり評価の結果は `07.feedback` に混ぜない。`eval/api_results/` または書き込み可能な退避先に保存する。

実行前確認:

- `python eval/run_static_checks.py` がPASSしていること。
- `python eval/run_offline_retrieval_eval.py --output-dir C:\VeriRAG_test\eval_local_results` がPASSしていること。
- 最新の `eval_local_results/offline_retrieval_*.jsonl` が存在すること。
- APIキーやsecrets値を標準出力、レポート、JSONLへ保存しないこと。
- 最小ケースから始め、コストが増えないようにすること。

dry-run:

```powershell
cd C:\VeriRAG_test
python eval/run_api_answer_eval.py --dry-run
python eval/run_api_answer_eval.py --dry-run --case-id G01 --case-id G08
```

dry-runではAPIを呼ばない。対象case、入力chunk、保存予定先、provider/model、G08の抑制評価対象であることを確認する。

API実行:

```powershell
cd C:\VeriRAG_test
python eval/run_api_answer_eval.py --run --case-id G01 --case-id G08 --output-dir C:\VeriRAG_test\eval_local_results
```

`--run` を明示した場合だけAPIを呼ぶ。`--all` はコストが増えるため、最小ケースの結果を確認してから使う。

代表ケース:

- G01: 単一文書・テスト管理。
- G03: 複数文書比較・課題管理/変更管理。
- G05: 複数文書統合・品質管理/測定分析。
- G08: 登録資料外質問・幻覚抑制。
- G10: 横断ケース・週次PMO報告ドラフト。

G08抑制評価の見方:

- `expected_sources` と `expected_chunk_ids` は空である。
- `must_abstain_if_missing=true` のため、回答に「登録資料上は確認できない」「確認できない」などの抑制表現が必要である。
- 外部規程番号や登録資料外の固有情報を断定した場合はwarningまたはrule flagとして扱う。

出力先:

- 優先順位は `--output`、`--output-dir`、`PMO_API_EVAL_OUTPUT_DIR`、`eval/api_results`、`eval_local_results`。
- APIキーやsecrets値は保存しない。
- prompt全文とchunk全文は結果JSONLに保存しない。
- dry-runでは結果JSONLを書かず、`eval/reports/api_answer_eval_sample.md` を更新する。

## 判定の読み方

- `Result: PASS`: 静的な前提は満たしている。回答品質を保証するものではない。
- `Warnings`: 既知の注意点。初期データでは `expected_chunk_ids` が空であることは許容する。
- `Errors`: スキーマ不整合、件数不整合、期待ソース未検出など。修正後に再実行する。
