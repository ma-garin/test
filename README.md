# qa-sentinel — 変更駆動 QA エージェント（枠組み・MVP・Codex 指示書）

開発中の CUD（追加・変更・削除）を起点に、影響分析 → テスト計画〜完了報告 → リグレッション差し替えを自律的に回すエージェント。
**mock で全部動く**（鍵なし）。**鍵（ANTHROPIC_API_KEY）を入れると本物の AI（Managed Agents）で動く**（下の「本番で動かす」）。Codex 環境ではそのまま zip を解凍して `RUN_ALL.md` の検証と実機確認だけを行う。

## 中身

| パス | 内容 |
|---|---|
| `docs/00_概要.md` | 何を解くか・決定事項・守る制約 |
| `docs/01_設計仕様.md` | 工程・判定表・ChangeEvent・台帳・env/config・ランタイム境界・Managed Agents 接続 |
| `docs/02_フロー.html` | 動作フローのアニメ（AI のレーンとあなたのレーン。止まる点で降りてきて、OK で戻る） |
| `docs/03_CLIデモ.html` | CLI デモ（4 問 → 進み具合 1 行 → あなたの番の帯 → ok / results / answer / approve） |
| `docs/04_画面案.html` | 画面案（始める 4 問・AI の下書きを直して OK・自分でやる段・やることリスト・誰がいつ決めたか） |
| `docs/05_協業モデル.md` | AI が下書き・人が直して確定。安全策 4 つ、開始時の 4 問、プリセット M1〜M8、状態 review/handoff |
| `docs/06_ループ設計.md` | ループエンジニアリング: 7 つのループの終了条件・上限・脱出先、8 つの技法、止まる条件の優先順位 |
| `.claude/skills/fanout-review-fix/` | 開発の段取りスキル（Sonnet 並列下書き → Opus レビュー → 本人修正 → Haiku git） |
| `docs/09_アニメ5案.html` | 進行表示 5 型の比較（ワークフロー・パイプライン・スイムレーン・かんばん・タイムライン）。製品画面と同じ JS |
| `docs/10_シナリオ確認.html` | 図書館デモを一巡した実画面 7 枚。各段で「あなたがすること／本物と仮／Codex 後・鍵後に変わること」 |
| `docs/11_GUI受け入れ確認.html` | Codex の zip を受け取ったとき GUI がこうなっていれば OK、のチェックリスト（8 項目）と増える 3 点の模型 |
| `docs/12_完成GUI.html` | 完成形の GUI がこの 1 枚で動く（サーバーなし・台帳はページ内・見本 4 件入り）。`python scripts/build_gui_standalone.py` で製品の index.html から生成 |
| `docs/08_進行表示の設計.md` | 画面の動き（アニメーション）の設計。参考にした事例（Devin・Copilot・Actions・Temporal・Vercel・HAX）と採用 |
| `docs/07_使い方.md` | **初めての人向け**: start.bat → 使い方画面 → 図書館デモ → やることリストを片づける（同じ内容が `/guide` で自動表示） |
| `incidents/` | インシデントの記録簿（`LOG.md`）と報告書（`INC-nnnn_*.md`）。再発防止は仕組みで担保し、検証してから対応済みにする |
| `start.bat` / `start.sh` | ダブルクリックで準備・起動・ブラウザを開く |
| `docs/codex/RUN_ALL.md` | Codex 環境での**検証と実機確認の指示書**（T01〜T09 はこの環境で実装済み。Codex は verify → デモ一巡 → 鍵があれば T08） |
| `demo/library-loan/` | 図書館デモ（単一 HTML）＋ kit のスクリプト・工程文書・ケース表を同梱。`run_gate` が呼ぶ実体 |
| `docs/codex/BUILD_GUIDE.md` | **Codex 環境で何をどの順にやれば構築できるか**（準備→実装 T01〜T07→道具の配布→実機確認 T08→運用） |
| `docs/codex/` | Codex 向け指示書 HANDOFF_T01〜T08 と雛形 |
| `qa_sentinel/` | 本体。`core/`（台帳・状態機械・orchestrator・gates・swap）、`runtime/`（mock と Managed Agents）、`triggers/`（nl・pr・tracker）、`agent/`（登録と system prompt）、`web/`（画面）、`cli.py`・`menu.py` |
| `projects/` | プロジェクトごとの環境情報（`env.md`）と設定（`config.toml`） |
| `scripts/verify.py` | 検証ゲート（唯一の実装。`verify.sh` は薄い呼び出し）。末尾 `ALL GREEN` で合格 |

## 初めての人はこれだけ

Windows: `start.bat` をダブルクリック（macOS/Linux: `./start.sh`）→ 初回は「使い方」の画面が自動で開く → 台帳の画面の「図書館デモを動かす」。詳しくは `docs/07_使い方.md`。
黒い画面なら `qa-sentinel demo --by 名前`、または `qa-sentinel` と打つだけで番号メニュー（5 がデモ）。進行表示は 5 つの型（ワークフロー／パイプライン／スイムレーン／かんばん／タイムライン）から画面で選べる。

## 本番で動かす（鍵あり: Managed Agents）

```bat
venv\Scripts\pip install -e .[managed]                       :: anthropic SDK
set ANTHROPIC_API_KEY=sk-ant-...                              :: 必須（ファイルに書かない）
set GITHUB_TOKEN=ghp_...                                      :: 任意。無いとリポジトリを積まずに動く
venv\Scripts\python -m qa_sentinel.agent.register --project library-loan   :: 1 回だけ。環境とエージェントを登録して agent.toml を書く
venv\Scripts\qa-sentinel run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更" --runtime managed
set QA_SENTINEL_RUNTIME=managed && start.bat                  :: 画面も本物で
```

守られること: 予算は `sessions.create` の budget（セント単位）で上限が効く。終了条件は `run_gate` ツールでホストが判定し、LLM は exit code を解釈しない。根拠の無い下書きは確定画面に出ない。承認は人だけ。

## ローカル LLM で動かす（Ollama。鍵不要）

```bat
ollama pull qwen2.5:14b                                      :: ツール呼び出しに対応したモデル（環境変数 OLLAMA_MODEL で変更）
venv\Scripts\qa-sentinel run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更" --runtime ollama
set QA_SENTINEL_RUNTIME=ollama && start.bat                   :: 画面も Ollama で
```

**実機未確認**（クラウド環境は ollama.com / registry.ollama.ai が組織方針で遮断。`docs/codex/OLLAMA_CHECK_RESULT.md`）。偽 HTTP のテスト 3 本のみ通過。サンドボックスは無い。書き込みは対象フォルダの `docs/`・`tests/`・`.qa-sentinel/` の下だけで、製品コードは `fix_proposal.md` に案を書く。上限は金額でなくトークン（`config.toml` の `[session] max_tokens`、既定 200000）。

## コマンドで試す（mock。LLM は使わない）

```bash
python3 -m venv venv && venv/bin/pip install -e .[dev]
venv/bin/qa-sentinel run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更"   # 開始時に 4 問（--mode M2 で省略可）
venv/bin/qa-sentinel status                                                              # 台帳一覧（確定待ち／手渡し／回答待ち／承認待ち）
venv/bin/qa-sentinel ok T-0001 --by yuki -m "ST-014 を直した"                            # AI の下書きを確定して次へ（reject で差し戻し）
venv/bin/qa-sentinel results T-0001 --by yuki --file results.csv                         # 自分でやった段の結果を渡す
venv/bin/qa-sentinel show T-0001                                                         # 履歴・根拠・誰が決めたか
venv/bin/qa-sentinel approve T-0001 --by yuki                                            # 最終承認
venv/bin/qa-sentinel web --port 8790                                                     # http://127.0.0.1:8790/ で同じ台帳を見る
venv/bin/python scripts/verify.py     # Windows: venv\Scripts\python scripts\verify.py
```

`--runtime managed` は Codex の T02 で実装される（現状は NotImplementedError）。
