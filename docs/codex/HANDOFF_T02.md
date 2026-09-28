# Codex への指示書 — T02: Managed Agents ランタイム

このファイル 1 枚をそのまま Codex に渡す。他の文書を読ませる必要はない（必要なものはここに書いてある）。

---

## 0. あなた（Codex）の役割

- このリポジトリ（qa-sentinel）の **指示書の範囲内の実装** を行う。ファイル編集まで。
- **git 操作はしない**（add / commit / push / branch を実行しない）。検証と commit は人（Claude）が行う。
- 「触るファイル」以外を編集しない。編集したくなったら、理由を最後の報告に書いて止まる。
- 推測で埋めない。分からないことは最後の報告に「質問」として書く。
- 完了の基準は 1 つ: `python scripts/verify.py`の末尾が `ALL GREEN`。

## 1. リポジトリの地図（これだけ知っていればよい）

```
qa_sentinel/runtime/base.py           Runtime プロトコル・SessionHandle・PhaseResult の定義（今回は触らない）
qa_sentinel/runtime/managed_agents.py 実装対象。ManagedAgentsRuntime のスタブ（NotImplementedError）
qa_sentinel/core/project.py           Project（config・env_md・budget_usd）
qa_sentinel/core/ledger.py            Task（task.branch）。今回は触らない
qa_sentinel/core/event.py             ChangeEvent。今回は触らない
qa_sentinel/core/gates.py             run_gate(name, args, project, cwd=".", timeout=600) -> GateResult（T01 提供）
qa_sentinel/agent/system_prompt.md    Managed Agents の system prompt（今回は触らない）
scripts/verify.py                     検証ゲート
```

背景: 各段の終了条件は `run_gate`（exit code）が機械判定し、LLM に解釈させない。Managed Agents は 1 イベント = 1 セッションで段を進める側。予算超過・終了は `stopped` として人へ渡る。

## 2. ゴール

`ManagedAgentsRuntime`（`start`/`run_phase`/`end`）を実装し、予算つきセッションを起動、段ごとに指示を送り、`run_gate` の custom tool をホストで処理し `PhaseResult` を返す。人待ちでは `end()` で終了する。`cli.py` は `ManagedAgentsRuntime()` を引数無しで生成するので、`agent_id`/`agent_version`（`projects/<name>/agent.toml`）と `environment_id`（`QA_SENTINEL_ENV_ID`）は `__init__` では読まず `start()` で遅延解決する。あわせて `qa_sentinel/agent/register.py`（`agents.create` を 1 回だけ行い id/version を `projects/<name>/agent.toml` に保存する CLI）を作る。

## 3. 触るファイル（これ以外は変更しない）

- `qa_sentinel/runtime/managed_agents.py`
- `qa_sentinel/agent/register.py` — 新規
- `tests/test_managed_runtime.py` — 新規（SDK はモック。実 HTTP は叩かない）

## 4. 実装の指示（推測の余地を残さない）

SDK は `anthropic`（`pip install -e .[managed]`）。名前を推測しない。

`__init__(self, project_name: str | None = None)`: `cli.py` は `ManagedAgentsRuntime()` を引数無しで呼ぶ。この時点では何も読み込まず `self.project_name = project_name`, `self.agent_id = self.agent_version = self.environment_id = None`, `self._client = None` を保持するだけにする（agent.toml・環境変数の解決は `start()` で遅延して行う）。

`start(task, event, project)`:
- まず環境変数チェック: `ANTHROPIC_API_KEY` / `GITHUB_TOKEN` / `QA_SENTINEL_ENV_ID` が 1 つでも無ければ `RuntimeError(f"環境変数が無い: {不足名の一覧}")`。`self.environment_id = os.environ["QA_SENTINEL_ENV_ID"]`。
- 次に `project.root / "agent.toml"` を読む（`load_project` が `root = projects/<name>` を設定済みなので、`self.project_name` や `projects/<name>` を自分で組み立てない。`project.root` をそのまま使う）。`register.py` が書いた `[agent]\nid\nversion` を `tomllib` で読み、`self.agent_id`, `self.agent_version` に入れる。無ければ `RuntimeError(f"agent.toml が無い: {path}（先に register.py で登録する）")`。
- `Anthropic()` を生成して `self._client` に保持。
- `env_file = client.beta.files.upload(file=(project.root/"env.md").open("rb"))`
- リポジトリ URL は `config["project"]["repo"]`（`"owner/repo"` 形式。`repo_url` というキーは無い）から組み立てる: `repo_url = "https://github.com/" + project.config["project"]["repo"]`。branch は `project.config["project"]["branch"]`（`task.branch` ではなくこちらを使う。作業ブランチへの書き込み制限は system prompt 側の指示で行う）。
- `client.beta.sessions.create(agent={"type":"agent","id":self.agent_id,"version":self.agent_version}, environment_id=self.environment_id, title=f"{task.task} {event.source}", budget={"type":"limit","max_list_cost":{"amount":project.budget_usd,"currency":"USD"}}, resources=[{"type":"github_repository","url":repo_url,"mount_path":"/workspace/repo","authorization_token":os.environ["GITHUB_TOKEN"],"branch":branch}, {"type":"file","file_id":env_file.id,"mount_path":"/workspace/env.md"}])`
- 戻り値 `SessionHandle(id=session.id, budget_usd=project.budget_usd)`

`run_phase(handle, phase, task, event, project)`:
- `client.beta.sessions.events.send(session_id=handle.id, events=[{"type":"user.message","content":[{"type":"text","text": prompt}]}])` を先に送る。`prompt` には「段 `<phase>`。ChangeEvent: `<event.to_json()>`。resume_from: `<event.resume_from>`」に加え、「終了時は `/mnt/session/outputs/phase_result.json` に `{"impact":[...],"cases_added":[...],"cases_retired":[...],"evidence":[...]}` を書くこと。書き込みは `task.branch` のみ。製品コードは直さず `/mnt/session/outputs/fix_proposal.md` に原因と直し方の案を書くこと」を含める（system prompt を触らない代わりにここで指示する）。
- `for ev in client.beta.sessions.events.stream(session_id=handle.id):` をループし、
  - `ev.type == "agent.custom_tool_use" and ev.name == "run_gate"`: `run_gate(ev.input["name"], ev.input.get("args", []), project, cwd=project.config["project"].get("subdir", "."))` を呼ぶ（`cwd` を渡さないと相対パスが解決できず、すべての gate が `exit_code=127` になる）。**`run_gate` は未知の名前や `impact` に ID 無しで `ValueError` を投げる**ので、ここを `try/except ValueError as e` で包み、失敗時は `exit_code=2, summary=str(e)` として扱う（成功時は `gate.exit_code`, `gate.summary`）。直近の gate 結果（`ok` の有無を含む）として保持し、`events.send` で `[{"type":"user.custom_tool_result","tool_use_id":ev.id,"content":{"summary":summary,"exit_code":exit_code}}]` を返す。**例外を `run_phase` の外へ出さない**。`name != "run_gate"` は無視して続行。
  - `ev.type == "session.status_idle"`: `spent = getattr(ev, "list_cost", None) or 0.0`。`ev.stop_reason == "budget_reached"` なら `PhaseResult("stopped","budget_reached", spent_usd=spent)`。それ以外は `phase_result.json` を取得する。**この JSON はセッションのコンテナ内にしか無い**ので、ホスト側は session files/outputs API 経由で取る: セッションのファイル一覧を取得し（例 `client.beta.sessions.files.list(session_id=handle.id)`）、`phase_result.json`（`outputs/phase_result.json` 相当のパス）があれば内容をダウンロードして `json.loads`、無ければ `{}` とする。テストはこの一覧・ダウンロード呼び出しをモックする。`evidence = data.get("evidence", [])`。**`evidence` が空なら `outcome="blocked", reason="no_evidence"`**（docs/05 の安全策: 根拠の無い下書きは確定に出さない）。空でなければ、直近の gate が `ok is False`（ValueError 経由の `exit_code=2` も含む。`cases` gate が `確認待ち` を含む場合も含む）のとき `outcome="blocked", reason=f"gate_ng:{gate名}"`、それ以外は `outcome="ok"`。`impact`/`cases_added`/`cases_retired`/`evidence` は `data` からそのまま詰め、`spent_usd=spent` を入れて返す。
  - `ev.type == "session.status_terminated"`: `PhaseResult("stopped","terminated")`。
- `spent_usd` は必ずセッション由来の値か `0.0`。推測値を入れない。
- system prompt には触らないので、`/mnt/session/outputs/phase_result.json` の書式指示は §4 冒頭の `prompt` にすでに含めている通り、エージェント側のパスは `/mnt/session/outputs/...`（コンテナ内絶対パス）のままでよい。ホスト側はそれをファイル API 越しに取得するだけで、パス文字列自体は変えない。

`end(handle)`: `client.beta.sessions.archive(handle.id)` を try/except で包み、例外を外に出さない。`handle.ended = True` は常に立てる。

`qa_sentinel/agent/register.py`（CLI）:
- `argparse` で `--project` を受ける。`Anthropic()` を作り、`qa_sentinel/agent/system_prompt.md` を読んで `client.beta.agents.create(name="qa-sentinel", model="claude-opus-5", system=<system_prompt>, tools=[{"type":"agent_toolset_20260401"}, {"type":"custom","name":"run_gate","description":"終了条件スクリプトを実行し exit code と要約を返す","input_schema":{"type":"object","properties":{"name":{"type":"string"},"args":{"type":"array","items":{"type":"string"}}},"required":["name"]}}])` を 1 回呼ぶ。
- `projects/<project>/agent.toml` に `[agent]\nid = "<agent.id>"\nversion = "<agent.version>"\n` を書き出す（既存があれば上書きしてよい。再登録用の明示コマンドのため）。

## 5. テスト（tests/test_managed_runtime.py）— この 4 ケースを書く

`unittest.mock.patch` で `qa_sentinel.runtime.managed_agents.Anthropic` を差し替える。実 HTTP は絶対に叩かない。`Project`/`Task`/`ChangeEvent` は最小のダミー（`Project(name="p", root=tmp_path, config={"project":{"repo":"owner/repo","branch":"main"}, "session":{"budget_usd":5.0}}, env_md="")`。`tmp_path` に `env.md` を置く）。

ケース 1〜3 では **事前に** `monkeypatch.setenv("ANTHROPIC_API_KEY", ...)` / `monkeypatch.setenv("GITHUB_TOKEN", ...)` / `monkeypatch.setenv("QA_SENTINEL_ENV_ID", ...)` を行い、かつ `project.root / "agent.toml"`（テストでは `project.root = tmp_path` なので `tmp_path/"agent.toml"`）に `[agent]\nid = "agt_1"\nversion = "1"\n` を書いてから `start()` を呼ぶ。

1. **budget を渡している**: `start()` を呼び、モックした `sessions.create` の `kwargs["budget"]` が `{"type":"limit","max_list_cost":{"amount":5.0,"currency":"USD"}}` であることを assert。あわせて `resources` 中の `github_repository.url` が `"https://github.com/owner/repo"` であることも assert。
2. **custom_tool_use → custom_tool_result**: `events.stream` が `agent.custom_tool_use`（`name="run_gate"`, `input={"name":"trace-check","args":[]}`）を 1 件、続けて `session.status_idle`（`stop_reason=None`）を yield するモック。session files/outputs 取得もモックして空（`{}`）を返させる。`run_gate` もモックして `ok=True` を返させ、`run_phase` 後に `events.send` が `user.custom_tool_result` を含む呼び出しで呼ばれたことを assert。
3. **budget_reached → stopped**: `events.stream` が `session.status_idle`（`stop_reason="budget_reached"`）だけを yield。戻り値が `outcome="stopped", reason="budget_reached"` であることを assert。
4. **環境変数欠落で RuntimeError**: `monkeypatch.delenv` で `ANTHROPIC_API_KEY` 等を外し、`start()` が `RuntimeError` を上げることを assert（この場合は agent.toml が無くても環境変数チェックが先に落ちるので用意しなくてよい）。

## 6. 完了条件

- [ ] `python scripts/verify.py` の末尾が `ALL GREEN`
- [ ] `python -m pytest -q tests/test_managed_runtime.py` が 4 passed
- [ ] 変更が「触るファイル」3 本に収まっている（`runtime/managed_agents.py`, `agent/register.py`, `tests/test_managed_runtime.py`）

## 7. スコープ外（やらないこと）

- 実 API を叩くテスト（Claude が API キーと予算 $1 で実機確認する）
- swap の実体（`regression-swap` 段の CSV 書き換えロジック本体。T03）
- `qa_sentinel/core/gates.py` 自体の実装（T01 提供。`run_gate` は既にある前提で使うだけ）
- git 操作

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
作ったファイル: <パス>, <パス>
verify.py の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
