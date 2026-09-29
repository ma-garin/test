# RUN_ALL — 一言で全部作る指示書（Codex: gpt-6-sol, reasoning=high）

人が Codex に貼るのはこの 1 文だけ:

> `docs/codex/RUN_ALL.md` に従って、止まらずに最後までやれ。

以下は Codex が読む本文。

---

## 0-W. 前提（Windows 11・zip・cmd・git なし・bash なし）

GitHub の「Code → Download ZIP」を解凍し、そのフォルダで cmd を開いて Codex（gpt-6-sol, high）を起動する。Python 3.11 以上だけあればよい。**git も bash も無い前提**で書いてある。

Codex に貼る文:

> `docs/codex/RUN_ALL.md` に従って、止まらずに最後までやれ。OS は Windows 11、シェルは cmd、git は無い。

成果の渡し方: 作業が終わったらフォルダごと zip にして人に渡す（`venv` と `__pycache__` は入れない）。取り込み（commit・PR）は人と Claude が行う。

## 0. あなた（Codex）の役割と権限

- このリポジトリ（qa-sentinel）を **動くところまで** 作る。実装・検証を自分で回す。人に聞かない（聞きたいことは §7 の報告に書いて先へ進む）。
- git は使わない（無い）。代わりに、タスクが 1 本終わるごとに `docs/codex/PROGRESS.md` に「Tnn DONE / SKIPPED(理由) / verify 末尾」を 1 行追記する（これが commit の代わり）。
- 秘密情報をファイルや報告に書かない。
- 各タスクの完了基準は 1 つ: `venv\Scripts\python scripts\verify.py` の末尾が `ALL GREEN`。
- 上限: 1 タスクにつき verify 失敗からのやり直しは **3 回**。3 回で通らなければそのタスクを `SKIPPED` にして次へ進み、§7 に理由を書く。

## 1. 準備（5 分）

```bat
python -m venv venv
venv\Scripts\pip install -e .[dev]
venv\Scripts\python scripts\verify.py            :: 末尾 ALL GREEN（MVP が動く）
```

同梱済みで、取りに行く必要が無いもの:
- `demo/library-loan/` — 図書貸出のデモアプリ（単一 HTML）＋ `scripts/`（Python: test_metrics.py・check_approval.py・pw-spec-lint.py・section_hash.py・test-weaken-check.py。`.sh` は Windows では使わない）＋ `docs/lifecycle/`（工程文書と追跡表）＋ `docs/test/`（ケース表・29119 文書）
- `projects/library-loan/` — その環境情報と設定

kit のスクリプトのうち `.sh` は動かない（bash なし）。Python 版を使う: `test_metrics.py`・`check_approval.py`・`pw-spec-lint.py`・`section_hash.py`。`trace_check.py` は **T09** で作る（それまで trace-check は「未検査」）。`test-weaken-check.py` は git が要るので常に「未検査」。

## 2. 実装（済み。読むだけ）

T01〜T07・T09 は **この zip に実装済み**（`docs/codex/PROGRESS.md`）。Codex が書くコードは無い。`verify.py` が ALL GREEN でなければ、落ちたテスト名と出力を §7 に書いて止まる（直そうとしない）。

## 3. デモを通す（mock ランタイムで一気通貫。鍵不要）

T07 まで終わったら、図書館デモで人の操作込みの一連を回し、動くことを示す:

```bat
set Q=venv\Scripts\qa-sentinel
%Q% demo --by codex
:: ↑ = run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更" --mode M2 --reviewer codex --budget 5
%Q% ok T-0001 --by codex -m "設計を確認"
:: ↑ 設計の出口 → 実行は人の段（手渡し）
%Q% results T-0001 --by codex --file demo/library-loan/docs/test/system_test_cases.csv -m "できた 3 / できなかった 0"
%Q% ok T-0001 --by codex
:: ↑ 入れ替えの出口
%Q% approve T-0001 --by codex
%Q% show T-0001
:: ↑ decisions が 4 件、evidence が段ごとにある
```

加えて、実際の差し替えが CSV に効くことを **swap（T03）で直接** 確かめる: `demo/library-loan/docs/test/system_test_cases.csv` に `根拠の版=REQ-F-001@...` の行を 1 つ足し、`core.swap.swap(project, ["REQ-F-001"], [<新ケース 1 件>], cwd="demo/library-loan")` を呼んで、その行の `仕様の状態` が `失効(ST-xxx)` になり、`venv\Scripts\python demo\library-loan\scripts\trace_check.py demo\library-loan\docs\lifecycle` が NG=0 のままであること。結果を `docs/codex/DEMO_RESULT.md` に貼る（コマンドと出力）。

Web も 1 回起動して確認（`start /b venv\Scripts\qa-sentinel web --port 8790` → `curl -s http://127.0.0.1:8790/api/tasks` が台帳を返す → ブラウザで `http://127.0.0.1:8790/guide`（使い方）と `http://127.0.0.1:8790/`（台帳。タスクが無いと「図書館デモを動かす」ボタンが出る。詳細の「表示の型」で 5 つの進行表示が切り替わる）を開く → その cmd を閉じて止める）。

## 4. 実機確認（鍵があるときだけ）

環境変数 `ANTHROPIC_API_KEY` が **設定されていれば** `HANDOFF_T08_live_check.md` を行う（予算 $1 で 1 段）。設定されていなければ飛ばし、§7 に「T08: 未実行（鍵なし）」と書く。鍵を探しに行かない・聞かない。

## 5. 仕上げ

- `README.md` の「すぐ試す」を、§3 で実際に通したコマンド列に更新（動かなかったものは書かない）
- 人に渡す zip の使い方は `start.bat` → 初回は使い方の画面（`/guide`）が自動で開く。ここは変えない
- `docs/codex/DEMO_RESULT.md` と（あれば）`LIVE_CHECK_RESULT.md` をフォルダに残す（commit は人が行う）
- `venv\Scripts\python scripts\verify.py` 最終 ALL GREEN
- フォルダごと zip にして人に渡す（`venv` と `__pycache__` は入れない）。PR は人と Claude が作る

## 6. 止まる条件（これ以外では止まらない）

| 条件 | 対処 |
|---|---|
| verify が 3 回通らない | そのタスクを SKIPPED、次へ |
| kit の Python スクリプトが動かない | §7 に書いて続ける（その gate は「未検査」として通す） |
| ネットワークが要る操作 | しない（PR 取得・JIRA はモックで） |

## 7. 最後の報告（この形で。10 行以内）

```
zip: <ファイル名>
T01,T09,T03,T02,T04,T05,T06,T07: 各 DONE / SKIPPED(理由)
デモ: §3 の 6 コマンドが通ったか / DEMO_RESULT.md のパス
swap 確認: 失効の印が付いたか / trace-check NG=0 か
Web: /api/tasks が返ったか
実機(T08): 未実行（指示）
verify 最終: ALL GREEN / 失敗ステップ
触るファイル外の変更: なし / <パス>（理由）
質問: なし / <内容>
```
