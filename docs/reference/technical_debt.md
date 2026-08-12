# Technical Debt

調査日: 2026-06-02

本ドキュメントは現状コードとデータ配置から確認できる技術的負債をまとめます。アプリ本体、既存データ、FAISSインデックス、`.streamlit/secrets.toml` は変更していません。

## 根拠ファイル

- `AGENTS.md`
- `README_ENV.md`
- `Readme.md`
- `05.app/pmo_agent_app.py`
- `05.app/rag_web_min.py`
- `99.scripts/doc_registry.py`
- `99.scripts/PDF2jsonLoader.py`
- `99.scripts/Office2jsonLoader.py`
- `99.scripts/build_faiss_min.py`
- `index_map.json`
- `04.faiss_index/chunks.json`
- `04.faiss_index/lexical_index.json`
- `template_registry.json`
- `07.feedback/*.jsonl`

## 重大リスク

| リスク | 根拠 | 影響 |
|---|---|---|
| 検証環境の台帳が本番パスを参照している | `index_map.json` の16件すべてが `C:\VeriRAG\...` 参照 | 検証環境で取込・再構築・管理操作を行う際、想定外に本番原本を参照する可能性がある |
| メインアプリが巨大単一ファイル | `05.app/pmo_agent_app.py` は約18,730行、657KB | 小変更でも副作用範囲が読みづらく、レビュー、テスト、障害切り分けが難しい |
| 管理パスワードがコードにハードコードされている | `ADMIN_PASSWORD` が固定文字列として定義されている | ソース閲覧者が管理画面へアクセスできる。変更時にコード差分へ残る |
| 管理画面に物理削除機能がある | `admin_permanently_delete_records()`, `remove_faiss_outputs()` | 誤操作時の復旧困難。台帳・原本・JSON・FAISS出力の整合性崩れが起きやすい |
| 設定画面の保存先説明が環境と混同しやすい | テストアプリ内の設定画面文言に `C:\VeriRAG\.streamlit\secrets.toml` が見える一方、コード上の `SECRETS_PATH` は `PROJECT_ROOT/.streamlit/secrets.toml` | テストと本番のどちらを変更しているか誤認する可能性がある |

## 保守性リスク

`pmo_agent_app.py` に、UI描画、CSS、データアクセス、RAG検索、LLM生成、履歴保存、Excel出力、設定保存、管理削除が同居しています。責務境界が弱いため、RAG品質改善の変更がUIやテンプレート出力へ波及しやすい状態です。

RAG検索の実装は高機能です。FAISS、語彙検索、クエリ拡張、LLM rerank、回答バランス、テンプレート文脈が組み合わさっていますが、個別の契約テストが見当たりません。機能追加時に「検索漏れが減ったのか、逆にノイズが増えたのか」を定量確認しづらいです。

`Readme.md` と一部スクリプトの日本語メッセージに文字化けが見られます。運用手順やエラー文が正しく読めないため、引き継ぎと障害対応で誤解が起きます。

`05.app` 配下には多数の `pmo_agent_app.py.bak_*` が残っています。履歴としては有用ですが、検索やレビュー時に現行ファイルと誤認しやすく、容量とノイズも増えています。

## セキュリティリスク

`.streamlit/secrets.toml` は開示禁止ファイルです。コード上は `load_app_secrets()` が読み、`save_app_secrets()` が書き戻します。保存時に `.toml.bak` を作る実装もあります。バックアップにも秘密値が含まれるため、除外ルールとアクセス制御が必要です。

`C:\VeriRAG_test` 直下には `.gitignore` が見当たりませんでした。Git管理の有無は `git` コマンドが環境になく未確認ですが、少なくともリポジトリ直下の除外ルールとして、`.streamlit/secrets.toml`、secretsバックアップ、`07.feedback`、FAISS生成物、テンプレート出力を除外する定義は確認できていません。

`07.feedback` 配下にはRAG回答履歴、チャット履歴、PMO相談履歴、テンプレート出力履歴、操作ログがあります。業務質問、回答、参照チャンク、成果物ドラフトを含む可能性があるため、共有・コミット・評価利用時の匿名化ルールが必要です。

Ollamaの接続先は `OLLAMA_BASE_URL` で変更可能です。ローカル利用前提ですが、設定次第で外部URLを向く可能性があります。接続先の許可範囲とログ出力方針を明確にする必要があります。

## テスト不足

現状、リポジトリ内に体系的なテストディレクトリは確認できませんでした。`99.scripts/check_*.py` は個別確認用スクリプトで、RAG品質、履歴保存、テンプレート出力、管理操作の回帰テストとしては不足しています。

不足しているテスト観点は以下です。

- `index_map.json` の状態遷移: `active`, `excluded`, `deleted`, `missing`
- PDF/Office変換後JSONのスキーマ検証
- チャンクID、ページ、シート、スライドなど引用メタデータの維持
- FAISS次元とEmbeddingモデル不一致時のエラー
- ハイブリッド検索の順位安定性
- LLM rerank失敗時のフォールバック
- RAG回答に参照チャンクが含まれること
- RAGチャット履歴の保存、復元、削除、複製
- PMO支援のローカルRAGあり/なしの出力差
- テンプレート出力の安全モード、マッピング、AI支援モード
- secrets値が画面やログに平文で出ないこと
- 管理画面の削除対象がプロジェクトルート外へ出ないこと

## 巨大ファイル問題

`05.app/pmo_agent_app.py` の巨大化により、次の問題が発生しています。

- 変更対象関数の探索に時間がかかる。
- Streamlit UIと業務ロジックが密結合している。
- ファイル単位レビューでは差分の意味を追いにくい。
- テストしやすい純粋関数と、UI副作用を持つ関数が分離されていない。
- `st.session_state` のキーが全域に散らばり、状態遷移の全体像が見えにくい。
- 例外処理、ログ、ユーザー表示メッセージ、内部処理が同じ関数内に混在する。

ただし、いきなり全面分割するとUI挙動や履歴互換性を壊す可能性が高いです。最初はパス・設定・JSONL・検索など副作用境界が明確な部分から、薄いモジュールへ段階的に切り出すべきです。

## 本番反映時の注意点

`README_ENV.md` と `AGENTS.md` により、日常利用は `C:\VeriRAG`、改善検証は `C:\VeriRAG_test`、受け入れ後に変更済みapp/scriptファイルだけ本番へコピーする運用です。

本番反映前に必ず確認すべき点は以下です。

- 変更対象がアプリ本体・スクリプト・ドキュメントのどれかを明示する。
- `C:\VeriRAG_test\index_map.json` に本番パスが混じる前提で、データファイルやインデックスを丸ごとコピーしない。
- `.streamlit/secrets.toml` とそのバックアップをコピー・表示・コミットしない。
- `07.feedback`、`04.faiss_index`、`03.json`、`00.input` は、明示依頼がない限り本番へコピーしない。
- Python変更時は `C:\VeriRAG\.venv\Scripts\python.exe -m py_compile` を実行する。
- RAG品質やEmbeddingモデルを変更した場合は、再構築前後のGolden Dataset評価を比較する。
- UI変更時は 8502 で確認し、受け入れ後だけ本番へ反映する。

## 未確認事項

- `git` がPowerShell上で利用できず、Git管理状態とコミット対象は確認できていません。
- Streamlit実画面での管理画面アクセス、削除操作、設定保存は未実施です。
- secrets値、OpenAI/Ollama疎通、実コスト、実APIエラーは未確認です。
- 既存履歴JSONLに含まれる業務情報の機微度は未確認です。
