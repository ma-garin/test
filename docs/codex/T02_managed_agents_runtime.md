# タスク: Managed Agents ランタイム — runtime/managed_agents.py を実装する

## ゴール
`qa-sentinel run --runtime managed` で、1 イベントにつき 1 セッションを予算つきで起動し、段ごとに指示を送り、`run_gate` の custom tool をホストで処理し、`PhaseResult` を返す。人待ちではセッションを終了する。

## 触るファイル（これ以外は変更しない）
- `qa_sentinel/runtime/managed_agents.py` — 実装
- `qa_sentinel/agent/register.py` — 新規。`agents.create` を 1 回だけ行い ID を `projects/<name>/agent.toml` に保存する CLI 補助（`python -m qa_sentinel.agent.register --project <p>`）
- `tests/test_managed_runtime.py` — 新規。SDK はモック（`unittest.mock`）にして HTTP を叩かない

## 実装の指示
呼び出し形は `docs/01_設計仕様.md` §6。SDK は `anthropic`（`pip install -e .[managed]`）。名前を推測しない。不明な引数は公式ドキュメント（Managed Agents Python）を読む。

- `start()`:
  - `client.beta.files.upload(file=<env.md>)` → `file_id`
  - `client.beta.sessions.create(agent={"type":"agent","id":agent_id,"version":version}, environment_id=..., title=f"{task.task} {event.source}", budget={"type":"limit","max_list_cost":{"amount":project.budget_usd,"currency":"USD"}}, resources=[github_repository(mount /workspace/repo, branch from config), file(mount /workspace/env.md)])`
  - `SessionHandle(id=session.id, budget_usd=project.budget_usd)`
- `run_phase()`:
  - `events.stream(session_id)` を先に開き、`user.message` で「段 <phase>。ChangeEvent: <JSON>。resume_from: ...」を送る
  - `agent.custom_tool_use` で `name == "run_gate"` なら `core.gates.run_gate(input["name"], input.get("args", []), project)` を実行し、`user.custom_tool_result` に `summary` と `exit_code` を返す
  - `session.status_idle`: `stop_reason == "budget_reached"` → `PhaseResult("stopped","budget_reached")`。それ以外は最後の gate 結果で `ok` / `blocked` を決める。`cases` gate が `確認待ち` を含めば `blocked`
  - `session.status_terminated` → `PhaseResult("stopped","terminated")`
  - `spent_usd` はセッションの `list_cost` があればそれ、無ければ 0.0（推測値を入れない）
  - `impact` / `cases_added` / `cases_retired` は、エージェントが `/mnt/session/outputs/phase_result.json` に書いた内容から読む（無ければ空）。system prompt にその書式を 1 行追記してよい（`qa_sentinel/agent/system_prompt.md` は触らず、送るメッセージ側で指示する）
- 協業モデル（docs/05）の守り: 書き込みは `task.branch`（`qa-sentinel/<task>`）にだけ。製品コードは修整せず「原因と直し方の案」を `/mnt/session/outputs/fix_proposal.md` に書かせる。`PhaseResult.evidence` に gate 名と exit code・参照した仕様の節・ログのパスを入れる（空なら `blocked` 扱いにして人へ）
- `end()`: `client.beta.sessions.archive(session_id)`。失敗しても例外を外に出さない（台帳には ended=True）
- 環境変数: `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `QA_SENTINEL_ENV_ID`。無ければ `start()` で `RuntimeError` に理由を書く

## 完了条件
- [ ] `bash scripts/verify.sh` が `ALL GREEN`
- [ ] `tests/test_managed_runtime.py`: budget を渡している / custom_tool_use に対して custom_tool_result を送る / budget_reached で stopped / 環境変数欠落で RuntimeError の 4 ケース（SDK はモック）
- [ ] 変更が上記「触るファイル」内に収まっている

## スコープ外（やらないこと）
- 実 API を叩くテスト（Claude が API キーと予算 $1 で実機確認する）
- swap の実体（T03）
- git 操作
