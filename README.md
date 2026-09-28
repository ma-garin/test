# qa-sentinel — 変更駆動 QA エージェント（枠組み・MVP・Codex 指示書）

開発中の CUD（追加・変更・削除）を起点に、影響分析 → テスト計画〜完了報告 → リグレッション差し替えを自律的に回すエージェントの**設計文書と MVP**。
本実装は `docs/codex/` の指示書に沿って Codex が行う（役割分担は `docs/codex/README.md`）。

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
| `docs/08_進行表示の設計.md` | 画面の動き（アニメーション）の設計。参考にした事例（Devin・Copilot・Actions・Temporal・Vercel・HAX）と採用 |
| `docs/07_使い方.md` | **初めての人向け**: start.bat → 画面で「始める」→ やることリストを片づける |
| `start.bat` / `start.sh` | ダブルクリックで準備・起動・ブラウザを開く |
| `docs/codex/RUN_ALL.md` | **一言で全部作る指示書**（Codex に「RUN_ALL.md に従え」と貼るだけ。T01〜T07 → 図書館デモ → 実機 → PR） |
| `demo/library-loan/` | 図書館デモ（単一 HTML）＋ kit のスクリプト・工程文書・ケース表を同梱。`run_gate` が呼ぶ実体 |
| `docs/codex/BUILD_GUIDE.md` | **Codex 環境で何をどの順にやれば構築できるか**（準備→実装 T01〜T07→道具の配布→実機確認 T08→運用） |
| `docs/codex/` | Codex 向け指示書 HANDOFF_T01〜T08 と雛形 |
| `qa_sentinel/` | MVP（台帳・状態機械・モックランタイム・CLI・Web） |
| `projects/` | プロジェクトごとの環境情報（`env.md`）と設定（`config.toml`） |
| `scripts/verify.py` | 検証ゲート（唯一の実装。`verify.sh` は薄い呼び出し）。末尾 `ALL GREEN` で合格 |

## 初めての人はこれだけ

Windows: `start.bat` をダブルクリック（macOS/Linux: `./start.sh`）→ ブラウザが開く → 「変更を伝えて始める」。詳しくは `docs/07_使い方.md`。
黒い画面なら `qa-sentinel` と打つだけで番号メニュー。

## コマンドで試す（MVP。LLM は使わない）

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
