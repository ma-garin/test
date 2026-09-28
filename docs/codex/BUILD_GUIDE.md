# Codex 環境での構築手順 — 何を、どの順に、どうやるか

このリポジトリ（qa-sentinel）を、Codex を実装担当にして動くところまで持っていく手順。人（あなた）がやること、Codex がやること、Claude がやることを分けて書く。

## 0. 一言で全部やらせる（推奨）

Codex（gpt-6-sol, high）に貼る文はこれだけ:

> `docs/codex/RUN_ALL.md` に従って、止まらずに最後までやれ。

**前提（決定 2026-09-28）**: Codex 環境は Windows 11・zip 解凍・cmd。**git も bash も無い**。実機接続（T08）は今回やらない（mock まで）。成果は zip で受け取り、取り込み（commit・PR）は Claude が行う。一言は「`docs/codex/RUN_ALL.md` に従って、止まらずに最後までやれ。OS は Windows 11、シェルは cmd、git は無い。」

RUN_ALL は §1〜§5 を Codex が自分で回す（verify → commit → 次のタスク）。人がやるのは、鍵 3 つを環境変数に置くこと（実機確認をするときだけ）と、最後の PR をマージすることの 2 つ。以下は手で分けてやるときの手順。

## 0-b. 全体像

```
[準備 §1]  clone → venv → verify.py ALL GREEN（MVP が動くことを確認。LLM なし）
[実装 §2]  HANDOFF_T01 → T03 → T02 → T04 → T05 → T06 → T07 を 1 本ずつ Codex に貼る
           各回: Codex が実装 → verify.py ALL GREEN → 人（または Claude）が diff を見て commit
[道具 §3]  （同梱済み）demo/library-loan/scripts/ に kit のスクリプト、docs/ に工程文書・ケース表
[実機 §4]  HANDOFF_T08 で Managed Agents に接続し、予算 $1 で 1 段だけ回す
[運用 §5]  自分のプロジェクトの env.md / config.toml を書き、4 問で始める
```

所要の目安: §1 10 分、§2 は 1 本あたり Codex 10〜30 分 × 7、§3 10 分、§4 20 分（API キーがあれば）、§5 30 分。

## 1. 準備（人）

```bash
git clone https://github.com/ma-garin/test qa-sentinel && cd qa-sentinel
python3 -m venv venv && venv/bin/pip install -e .[dev]
venv/bin/python scripts/verify.py        # 末尾 ALL GREEN
venv/bin/qa-sentinel run --project library-loan --nl "貸出上限を 3 冊に" --mode M2 --reviewer <あなたの名前>
venv/bin/qa-sentinel status                        # 確定待ち が 1 行出れば MVP は動いている
```

ここまでで LLM も API キーも不要。動かなければ Python が 3.11 未満。

## 2. 実装（Codex に 1 本ずつ）

順序と依存: **T01 → T03 → T02 → T04 → T05 → T06 → T07**（T02・T03・T05 は T01 の `run_gate` を前提にする）。

各回のやり方（webspec2doc の `CODEX_GUIDE.md` と同じ決定的ループ）:

1. `docs/codex/HANDOFF_Tnn.md` の全文を Codex のプロンプトに貼る（`codex exec` でも同じ。作業ディレクトリはリポジトリ直下）
2. Codex は「触るファイル」だけを編集し、最後に §8 の 5 行報告を返す
3. あなた（または Claude）が `venv/bin/python scripts/verify.py` を実行し ALL GREEN を確認
4. `git diff` を読む。指示書の「触るファイル」の外に変更があれば戻す
5. `git add <パス>` → `git commit` → `git push`（Codex には git をさせない）

Codex が「質問」を返したら、指示書に答えを追記してから再実行する（会話で答えない。次の人が同じ質問をする）。

| # | 指示書 | できあがるもの | 前提 |
|---|---|---|---|
| T01 | `HANDOFF_T01.md` | `core/gates.py`（終了条件スクリプトの実行器） | — |
| T03 | `HANDOFF_T03.md` | `core/swap.py`（古いテストの失効・追記・戻し） | T01 |
| T02 | `HANDOFF_T02.md` | `runtime/managed_agents.py`、`agent/register.py` | T01 |
| T04 | `HANDOFF_T04.md` | `triggers/pr.py`、`run --pr` | — |
| T05 | `HANDOFF_T05.md` | 承認の機械判定、確定画面の差分表示・根拠ガード | T01 |
| T06 | `HANDOFF_T06.md` | `triggers/tracker.py`、`watch`（LLM を使わない監視） | T04 |
| T07 | `HANDOFF_T07.md` | ループ上限の判定（差し戻し 3・確認待ち 5・セッション 10） | T05 |
| T09 | `HANDOFF_T09_trace_check_py.md` | `trace_check.py`（bash 版の Python 移植。git/bash の無い環境で gate を動かす） | T01 |

## 3. 道具を配る（人）— `run_gate` が呼ぶスクリプトの実体

`run_gate` は `./scripts/trace-check.sh` などを呼ぶが、その実体は **yuki-aidd-kit** にある。対象プロジェクト（まずは図書貸出サンプル）に配る。

```bash
git clone https://github.com/ma-garin/yuki-aidd-kit ../yuki-aidd-kit
cd ../yuki-aidd-kit
./00_導入/02_プロジェクト配布/export-project.sh <対象プロジェクトのパス>     # scripts/ に trace-check.sh 等が入る
./00_導入/02_プロジェクト配布/init-lifecycle.sh <対象プロジェクトのパス>     # docs/lifecycle/（追跡表など）
./00_導入/02_プロジェクト配布/init-test-docs.sh <対象プロジェクトのパス>     # docs/quality/（system_test_cases.csv など）
```

図書貸出サンプルは `yuki-aidd-kit/01_利用者向け資料/90_サンプル/図書貸出/`。これを 1 つのプロジェクトとして切り出し、上の 3 つを当てる。`projects/library-loan/config.toml` の `repo`/`subdir` はその場所を指す。

確認: 対象プロジェクトで `./scripts/trace-check.sh docs/lifecycle` が動き `NG=` の行を出す。

## 4. 実機確認（Codex、HANDOFF_T08）

`docs/codex/HANDOFF_T08_live_check.md` を貼る。必要なもの（人が用意）: `ANTHROPIC_API_KEY`、`GITHUB_TOKEN`、`QA_SENTINEL_ENV_ID`（無ければ T08 §4-1 で作る）、`pip install -e .[managed]`。
予算 $1 で test-plan 1 段だけ回し、`evidence` と `spent_usd` が台帳に入り、$0.05 で `budget_reached` で止まることを確かめる。結果は `docs/codex/LIVE_CHECK_RESULT.md` に残る。

## 5. 自分のプロジェクトで使う（人）

1. `projects/_template/` を `projects/<name>/` にコピー
2. `env.md` を埋める（SUT の URL・起動手順・削除禁止・アカウントは vault 参照名だけ・API・既知の制約）。**空欄があるとセッションは起動しない**
3. `config.toml` を埋める（`repo`/`branch`、`lifecycle_dir`、`cases_csv`、`test_cmd`、`[triggers]`、`[session] budget_usd`）
4. §3 の 3 スクリプトをそのプロジェクトに当てる
5. 始める: `qa-sentinel run --project <name> --nl "<変更の説明>"` → 4 問（どこまで AI／どこで止まる／だれが見る／いくら）→ 止まったら `status` / `web` で確認して `ok` / `results` / `answer` / `approve`
6. 監視を使うなら `qa-sentinel watch --project <name>`（社内 JIRA は relay。`credential_ref = "env:JIRA_TOKEN"`）。常駐は systemd/launchd で人が行う。自己ウェイクの仕組みは作らない

## 6. 困ったとき

| 症状 | 見るところ |
|---|---|
| `env.md がない` で止まる | §5-2。環境情報を渡さないと起動しない設計 |
| `run_gate` が `exit 127 missing` | §3 のスクリプト配布が未了 |
| 確定待ちのまま進まない | 人の番。`qa-sentinel status` で誰の番かを見る。AI は待っていない |
| `budget_reached` | 予算を上げて `run --resume`（T02 以降）。上げるかは人が決める |
| 同じ段で 3 回差し戻し / 確認待ち 5 件 / セッション 10 本で `stopped` | 進め方の見直し（`docs/06_ループ設計.md` §3） |
