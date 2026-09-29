"""ManagedAgentsRuntime のテスト。SDK は偽物に差し替え、実 HTTP は叩かない（anthropic 未導入でも通る）。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from qa_sentinel.agent import register as reg
from qa_sentinel.core.event import ChangeEvent
from qa_sentinel.core.ledger import Task
from qa_sentinel.core.project import Project
from qa_sentinel.runtime.managed_agents import ManagedAgentsRuntime

MOD = "qa_sentinel.runtime.managed_agents"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "env.md").write_text("# env\n", encoding="utf-8")
    return Project(name="p", root=tmp_path, env_md="",
                   config={"project": {"repo": "owner/repo", "branch": "main"}, "session": {"budget_usd": 5.0}})


@pytest.fixture
def task():
    return Task(task="T-0001", project="p", event="evt_1", branch="qa/T-0001")


@pytest.fixture
def event():
    return ChangeEvent(id="evt_1", project="p", source="nl", text="貸出上限を 3 冊に")


@pytest.fixture
def env(monkeypatch, project):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test")
    monkeypatch.setenv("QA_SENTINEL_ENV_ID", "env_1")
    (project.root / "agent.toml").write_text('[agent]\nid = "agt_1"\nversion = "1"\n', encoding="utf-8")


def _fake_client(events=(), files=()):
    c = MagicMock()
    c.beta.files.upload.return_value = SimpleNamespace(id="file_1")
    c.beta.sessions.create.return_value = SimpleNamespace(id="sess_1")
    c.beta.sessions.events.stream.return_value = iter(events)
    c.beta.files.list.return_value = SimpleNamespace(data=list(files))
    return c


def _start(client, task, event, project):
    with patch(f"{MOD}.Anthropic", return_value=client):
        rt = ManagedAgentsRuntime()
        return rt, rt.start(task, event, project)


def _sent_types(client):
    return [e["type"] for call in client.beta.sessions.events.send.call_args_list for e in call.kwargs["events"]]


def test_start_passes_budget_and_repo(env, task, event, project):
    client = _fake_client()
    _, handle = _start(client, task, event, project)
    kw = client.beta.sessions.create.call_args.kwargs
    assert kw["budget"] == {"type": "limit", "max_list_cost": {"amount": "500", "currency": "USD"}}
    repo = next(r for r in kw["resources"] if r["type"] == "github_repository")
    assert repo["url"] == "https://github.com/owner/repo"
    assert kw["agent"] == {"type": "agent", "id": "agt_1", "version": "1"}
    assert handle.id == "sess_1" and handle.budget_usd == 5.0


def test_custom_tool_use_answered_with_gate_result(env, task, event, project):
    events = [SimpleNamespace(type="agent.custom_tool_use", id="tu_1", name="run_gate",
                              input={"name": "trace-check", "args": []}),
              SimpleNamespace(type="session.usage", list_cost={"amount": "30", "currency": "USD"}),
              SimpleNamespace(type="session.status_idle", stop_reason={"type": "end_turn"})]
    client = _fake_client(events)
    rt, handle = _start(client, task, event, project)
    gate = SimpleNamespace(ok=True, exit_code=0, skipped=False, summary="NG=0")
    with patch("qa_sentinel.core.gates.run_gate", return_value=gate) as rg:
        r = rt.run_phase(handle, "test-plan", task, event, project)
    rg.assert_called_once()
    assert _sent_types(client) == ["user.message", "user.custom_tool_result"]
    result = client.beta.sessions.events.send.call_args_list[1].kwargs["events"][0]
    assert result["type"] == "user.custom_tool_result" and result["custom_tool_use_id"] == "tu_1" and result["is_error"] is False
    assert json.loads(result["content"][0]["text"]) == {"gate": "trace-check", "exit_code": 0, "ok": True, "summary": "NG=0"}
    assert (r.outcome, r.reason, r.spent_usd) == ("blocked", "no_evidence", 0.3)  # 結果ファイルが空 → 根拠なし


def test_budget_reached_is_stopped(env, task, event, project):
    client = _fake_client([SimpleNamespace(type="session.usage", list_cost={"amount": "500", "currency": "USD"}), SimpleNamespace(type="session.status_idle", stop_reason={"type": "budget_reached"})])
    rt, handle = _start(client, task, event, project)
    r = rt.run_phase(handle, "test-plan", task, event, project)
    assert (r.outcome, r.reason) == ("stopped", "budget_reached")


def test_missing_api_key_raises_and_github_token_is_optional(monkeypatch, task, event, project):
    for k in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "QA_SENTINEL_ENV_ID"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ManagedAgentsRuntime(client=_fake_client()).start(task, event, project)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (project.root / "agent.toml").write_text('[agent]\nid = "agt_1"\nversion = "1"\n[environment]\nid = "env_toml"\n', encoding="utf-8")
    client = _fake_client()
    rt = ManagedAgentsRuntime(client=client)
    rt.start(task, event, project)
    kw = client.beta.sessions.create.call_args.kwargs
    assert kw["environment_id"] == "env_toml" and [r["type"] for r in kw["resources"]] == ["file"] and rt.repo_mounted is False


def test_structured_result_question_and_end(env, task, event, project):
    ok_data = {"impact": ["BD-002"], "cases_added": ["ST-013"], "cases_retired": [], "artifacts": {"plan": "p.md"},
               "evidence": ["trace-check exit 0"], "draft": {"title": "t", "text": "x"}, "question": None}
    client = _fake_client(
        [SimpleNamespace(type="session.usage", list_cost={"amount": "100", "currency": "USD"}), SimpleNamespace(type="session.status_idle", stop_reason={"type": "end_turn"})],
        files=[SimpleNamespace(id="f_1", filename="phase_result.json")])
    client.beta.files.download.return_value = json.dumps(ok_data).encode()
    rt, handle = _start(client, task, event, project)
    r = rt.run_phase(handle, "test-plan", task, event, project)
    assert (r.outcome, r.impact, r.cases_added, r.artifacts, r.draft) == ("ok", ["BD-002"], ["ST-013"], {"plan": "p.md"}, ok_data["draft"])

    q = dict(ok_data, question="期待結果はどちら？")
    msg = SimpleNamespace(type="agent.message", content=[SimpleNamespace(type="text", text=f"```json\n{json.dumps(q)}\n```")])
    client.beta.sessions.events.stream.return_value = iter([msg, SimpleNamespace(type="session.status_idle", stop_reason=None)])
    client.beta.files.list.return_value = SimpleNamespace(data=[])
    r = rt.run_phase(handle, "test-design", task, event, project)
    assert r.outcome == "blocked" and "期待結果はどちら" in r.reason

    client.beta.sessions.archive.side_effect = RuntimeError("boom")
    rt.end(handle)
    client.beta.sessions.archive.assert_called_once_with("sess_1")
    assert handle.ended


def test_register_writes_agent_toml(tmp_path):
    client = MagicMock()
    client.beta.agents.create.return_value = SimpleNamespace(id="agt_9", version="3")
    client.beta.environments.create.return_value = SimpleNamespace(id="env_7")
    out = reg.register("p", tmp_path, client=client)
    assert out.read_text(encoding="utf-8") == '[agent]\nid = "agt_9"\nversion = "3"\n\n[environment]\nid = "env_7"\n'
    assert client.beta.environments.create.call_args.kwargs["config"]["type"] == "cloud"
    kw = client.beta.agents.create.call_args.kwargs
    assert kw["model"] == "claude-opus-5" and '"question"' in kw["system"]
    assert [t.get("name") for t in kw["tools"]] == [None, "run_gate"]
