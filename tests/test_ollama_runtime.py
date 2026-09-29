"""OllamaRuntime のテスト。HTTP は偽の post 関数に差し替える（Ollama 無しで通る）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qa_sentinel.core.event import ChangeEvent
from qa_sentinel.core.ledger import Task
from qa_sentinel.core.project import Project
from qa_sentinel.runtime.ollama import OllamaRuntime


@pytest.fixture
def project(tmp_path):
    (tmp_path / "docs").mkdir(); (tmp_path / "src").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("# 仕様\n貸出上限 5 冊", encoding="utf-8")
    (tmp_path / "src" / "app.py").write_text("LIMIT = 5\n", encoding="utf-8")
    return Project(name="p", root=tmp_path, env_md="URL: http://sut", config={"project": {"subdir": str(tmp_path)}, "session": {"budget_usd": 5.0, "max_tokens": 1000}})


def _msg(content="", tool_calls=None, tokens=10):
    return {"message": {"role": "assistant", "content": content, "tool_calls": tool_calls or []}, "prompt_eval_count": tokens, "eval_count": tokens}


def test_tool_loop_then_json_result(project, monkeypatch):
    final = {"impact": ["BD-002"], "cases_added": ["ST-013"], "cases_retired": [], "artifacts": {}, "evidence": ["spec.md 貸出上限"], "draft": {"title": "t", "text": "x"}, "question": None}
    script = iter([
        _msg(tool_calls=[{"function": {"name": "list_files", "arguments": {"path": "."}}}]),
        _msg(tool_calls=[{"function": {"name": "read_file", "arguments": {"path": "docs/spec.md"}}},
                         {"function": {"name": "write_file", "arguments": {"path": "src/app.py", "content": "LIMIT = 3"}}},
                         {"function": {"name": "write_file", "arguments": {"path": "docs/plan.md", "content": "計画"}}},
                         {"function": {"name": "run_gate", "arguments": {"name": "trace-check", "args": []}}}]),
        _msg(content=f"done\n```json\n{json.dumps(final)}\n```"),
    ])
    seen = []
    def post(url, body):
        seen[:] = list(body["messages"])
        return next(script)
    from qa_sentinel.core import gates
    monkeypatch.setattr(gates, "run_gate", lambda *a, **k: gates.GateResult("trace-check", 0, True, "NG=0"))
    rt = OllamaRuntime(model="m", post=post)
    task = Task(task="T-0001", project="p", event="e"); ev = ChangeEvent(id="e", project="p", source="nl", text="上限 3 冊")
    h = rt.start(task, ev, project)
    r = rt.run_phase(h, "test-plan", task, ev, project)
    assert (r.outcome, r.impact, r.cases_added, r.draft["title"]) == ("ok", ["BD-002"], ["ST-013"], "t")
    tool_outputs = [m["content"] for m in seen if m.get("role") == "tool"]
    assert any("docs/spec.md" in t for t in tool_outputs)                     # list_files
    assert any(t.startswith("error: write not allowed") for t in tool_outputs)  # 製品コードは書けない
    assert (project.root / "src" / "app.py").read_text(encoding="utf-8") == "LIMIT = 5\n"
    assert (project.root / "docs" / "plan.md").read_text(encoding="utf-8") == "計画"
    assert any('"ok": true' in t for t in tool_outputs)                        # run_gate はホストが判定
    assert any(e.startswith("ollama: m tokens") for e in r.evidence)


def test_question_blocks_and_no_evidence_blocks(project):
    q = {"impact": [], "cases_added": [], "cases_retired": [], "artifacts": {}, "evidence": ["x"], "draft": None, "question": "期待結果は？"}
    rt = OllamaRuntime(model="m", post=lambda u, b: _msg(content=f"```json\n{json.dumps(q)}\n```"))
    task = Task(task="T-0001", project="p", event="e"); ev = ChangeEvent(id="e", project="p", source="nl", text="x")
    r = rt.run_phase(rt.start(task, ev, project), "test-design", task, ev, project)
    assert r.outcome == "blocked" and "期待結果は" in r.reason
    rt2 = OllamaRuntime(model="m", post=lambda u, b: _msg(content="根拠なし"))
    r2 = rt2.run_phase(rt2.start(task, ev, project), "test-design", task, ev, project)
    assert (r2.outcome, r2.reason) == ("blocked", "no_evidence")


def test_token_budget_and_unreachable(project):
    rt = OllamaRuntime(model="m", post=lambda u, b: _msg(tool_calls=[{"function": {"name": "list_files", "arguments": {"path": "."}}}], tokens=600))
    task = Task(task="T-0001", project="p", event="e"); ev = ChangeEvent(id="e", project="p", source="nl", text="x")
    r = rt.run_phase(rt.start(task, ev, project), "test-plan", task, ev, project)
    assert (r.outcome, r.reason) == ("stopped", "budget_reached")
    import urllib.error
    def down(u, b):
        raise urllib.error.URLError("connection refused")
    rt2 = OllamaRuntime(model="m", post=down)
    r2 = rt2.run_phase(rt2.start(task, ev, project), "test-plan", task, ev, project)
    assert r2.outcome == "stopped" and "unreachable" in r2.reason
