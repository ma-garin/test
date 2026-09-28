# RUN_ALL — 一言で全部作る指示書（Codex: gpt-6-sol, reasoning=high）

人が Codex に貼るのはこの 1 文だけ:

> `docs/codex/RUN_ALL.md` に従って、止まらずに最後までやれ。

以下は Codex が読む本文。

---

## 0-W. Windows 11 ＋ zip で始める場合（人が最初に 1 回だけ）

GitHub の「Code → Download ZIP」で取得し、解凍したフォルダで **cmd** を開き、そこで Codex を起動する。前提: Python 3.11 以上（`python --version`）。Git for Windows は任意（あると kit の `.sh` 検査が有効になり、`git` で commit できる）。

Codex に貼る文は同じ:

> `docs/codex/RUN_ALL.md` に従って、止まらずに最後までやれ。OS は Windows 11、シェルは cmd。

Windows での読み替え（本文の bash 表記は次に置き換える）:

| 本文 | Windows（cmd） |
|---|---|
| `venv/bin/python` / `venv/bin/qa-sentinel` | `venv\Scripts\python` / `venv\Scripts\qa-sentinel` |
| `PY=venv/bin/python bash scripts/verify.sh` | `venv\Scripts\python scripts\verify.py` |
| `./scripts/trace-check.sh …`（デモ内） | Git for Windows があれば `bash scripts\trace-check.sh …`。無ければ飛ばす（verify.py が「未検査」と出す） |
| `git checkout -b codex/build-…` | `.git` が無い（zip）なら先に `git init` → `git add -A` → `git commit -m baseline`。git 自体が無ければ commit は省略し、§7 に「git なし」と書く |
| PR を作る | 作れない。代わりに `git format-patch main --stdout > codex-build.patch`（git あり）か、フォルダを zip にして人に渡す |
| `$Q web --port 8790 &` → `curl` | `start /b venv\Scripts\qa-sentinel web --port 8790` → `curl -s http://127.0.0.1:8790/api/tasks`（curl は Windows 10 以降に同梱）→ `taskkill /f /im python.exe` は使わない（他の python を巻き込む）。ブラウザで開いて確認し、cmd を閉じて止める |

## 0. あなた（Codex）の役割と権限

- このリポジトリ（qa-sentinel）を **動くところまで** 作る。実装・検証・commit を自分で回す。人に聞かない（聞きたいことは §7 の報告に書いて先へ進む）。
- git は **作業ブランチ `codex/build-<日付>`** でだけ使う。`git add <パス明示>` → `commit`。`main` への push・force・reset・rebase は禁止。最後に PR を 1 本作る（`gh pr create --fill --base main` が使えれば。無ければ §7 にブランチ名を書く）。
- 秘密情報をファイルや報告に書かない。
- 各タスクの完了基準は 1 つ: `PY=venv/bin/python bash scripts/verify.sh`（Windows: `venv\Scripts\python scripts\verify.py`）の末尾が `ALL GREEN`。
- 上限: 1 タスクにつき verify 失敗からのやり直しは **3 回**。3 回で通らなければそのタスクを `SKIPPED` にして次へ進み、§7 に理由を書く。

## 1. 準備（5 分）

```bash
python3 -m venv venv && venv/bin/pip install -e .[dev]
PY=venv/bin/python bash scripts/verify.sh          # ALL GREEN（MVP が動く）
git checkout -b codex/build-$(date +%Y%m%d)
```

同梱済みで、取りに行く必要が無いもの:
- `demo/library-loan/` — 図書貸出のデモアプリ（単一 HTML）＋ `scripts/`（trace-check.sh・test-metrics.sh・test-weaken-check.py・check-approval.sh・pw-spec-lint.py・section_hash.py）＋ `docs/lifecycle/`（工程文書と追跡表）＋ `docs/test/`（ケース表・29119 文書）
- `projects/library-loan/` — その環境情報と設定

確認: `cd demo/library-loan && ./scripts/trace-check.sh docs/lifecycle` が `NG=0`。

## 2. 実装（T01 → T03 → T02 → T04 → T05 → T06 → T07）

各 `docs/codex/HANDOFF_Tnn.md` を **上から順に読んで実装** する。1 本ごとに:

```bash
PY=venv/bin/python bash scripts/verify.sh | tail -1     # ALL GREEN
git add <HANDOFF の「触るファイル」に列挙されたパスだけ>
git commit -m "feat(Tnn): <HANDOFF のタイトル>"
```

- HANDOFF の「触るファイル」の外は触らない。触らないと通らないときは、最小の変更にして §7 に書く。
- HANDOFF に「Claude が行う」「人が用意」とある項目は、**あなたがやる**（このファイルの権限が優先）。ただし §4 の鍵は人しか用意できない。
- T02 の `run_gate` は `demo/library-loan/scripts/` の実体を呼ぶ。`cwd` は `projects/library-loan/config.toml` の `subdir`（`demo/library-loan`）。

## 3. デモを通す（mock ランタイムで一気通貫。鍵不要）

T07 まで終わったら、図書館デモで人の操作込みの一連を回し、動くことを示す:

```bash
Q="venv/bin/qa-sentinel"
$Q run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更" --mode M2 --reviewer codex --budget 5
$Q ok T-0001 --by codex -m "設計を確認"                # 設計の出口 → 実行は人の段（手渡し）
$Q results T-0001 --by codex --file demo/library-loan/docs/test/system_test_cases.csv -m "できた 3 / できなかった 0"
$Q ok T-0001 --by codex                                # 入れ替えの出口
$Q approve T-0001 --by codex
$Q show T-0001                                         # decisions が 4 件、evidence が段ごとにある
```

加えて、実際の差し替えが CSV に効くことを **swap（T03）で直接** 確かめる: `demo/library-loan/docs/test/system_test_cases.csv` に `根拠の版=REQ-F-001@...` の行を 1 つ足し、`core.swap.swap(project, ["REQ-F-001"], [<新ケース 1 件>], cwd="demo/library-loan")` を呼んで、その行の `仕様の状態` が `失効(ST-xxx)` になり `trace-check.sh` が NG=0 のままであること。結果を `docs/codex/DEMO_RESULT.md` に貼る（コマンドと出力）。

Web も 1 回起動して `/api/tasks` が台帳を返すことを確認（`$Q web --port 8790 &` → `curl -s localhost:8790/api/tasks | head -c 300` → 停止）。

## 4. 実機確認（鍵があるときだけ）

`ANTHROPIC_API_KEY` `GITHUB_TOKEN` `QA_SENTINEL_ENV_ID` が **3 つとも** 環境変数にあれば `docs/codex/HANDOFF_T08_live_check.md` を実行（予算 $1）。1 つでも無ければ **やらない** で §7 に「未実行: 不足 <名前>」と書く。鍵を探しに行かない。

## 5. 仕上げ

- `README.md` の「すぐ試す」を、§3 で実際に通したコマンド列に更新（動かなかったものは書かない）
- `docs/codex/DEMO_RESULT.md` と（あれば）`LIVE_CHECK_RESULT.md` を commit
- `PY=venv/bin/python bash scripts/verify.sh` 最終 ALL GREEN
- PR を作る（できなければブランチ名を報告）

## 6. 止まる条件（これ以外では止まらない）

| 条件 | 対処 |
|---|---|
| verify が 3 回通らない | そのタスクを SKIPPED、次へ |
| `demo/library-loan/scripts/*.sh` が無い・動かない | §1 の同梱物が壊れている。§7 に書いて T01〜T07 は続ける（gate はモックで通す） |
| 鍵が無い | §4 を飛ばす |
| 途中でお金が $1 を超える | 即停止して報告 |

## 7. 最後の報告（この形で。10 行以内）

```
ブランチ: codex/build-YYYYMMDD   PR: <URL or なし>
T01..T07: 各 DONE / SKIPPED(理由)
デモ: §3 の 6 コマンドが通ったか / DEMO_RESULT.md のパス
swap 確認: 失効の印が付いたか / trace-check NG=0 か
Web: /api/tasks が返ったか
実機(T08): 合格 / 不合格 / 未実行（不足 <名前>）
verify 最終: ALL GREEN / 失敗ステップ
触るファイル外の変更: なし / <パス>（理由）
質問: なし / <内容>
```
