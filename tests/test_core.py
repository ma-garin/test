"""MVP の固定テスト。状態機械・台帳・司令塔（モック）・トリガー・CLI。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from qa_sentinel.cli import main
from qa_sentinel.core.event import ChangeEvent
from qa_sentinel.core.ledger import Ledger, Task
from qa_sentinel.core.orchestrator import answer, approve, run_event
from qa_sentinel.core.project import load_project
from qa_sentinel.core.state import PHASES, next_phase, phases_from
from qa_sentinel.runtime.mock import MockRuntime
from qa_sentinel.triggers import nl

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ws(tmp_path):
    shutil.copytree(ROOT / "projects", tmp_path / "projects")
    return tmp_path


def _project():
    return load_project("library-loan", ROOT / "projects")


def test_phases_order_and_next():
    assert PHASES[0] == "test-plan" and PHASES[-1] == "regression-swap"
    assert next_phase("test-plan") == "test-analysis"
    assert next_phase("regression-swap") is None
    assert phases_from("test-design")[0] == "test-design"
    with pytest.raises(ValueError):
        phases_from("nope")


def test_nl_trigger_extracts_ids():
    ev = nl.from_text("library-loan", "REQ-F-003 の上限を変更。DD-004 も")
    assert ev.source == "nl" and ev.changed_ids == ["DD-004", "REQ-F-003"]
    with pytest.raises(ValueError):
        nl.from_text("p", "   ")


def test_event_rejects_unknown_source():
    with pytest.raises(ValueError):
        ChangeEvent(id="e", project="p", source="slack")


def test_ledger_roundtrip(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = Task(task=led.new_id(), project="p", event="e")
    t.record("test-plan", "running")
    led.save(t)
    back = led.load(t.task)
    assert back.task == "T-0001" and back.history[-1]["phase"] == "test-plan"
    with pytest.raises(ValueError):
        t.record("test-plan", "weird")


def test_run_event_walks_all_phases_and_waits_for_human(tmp_path):
    led = Ledger(tmp_path / "tasks")
    ev = nl.from_text("library-loan", "貸出上限を 5 冊から 3 冊に変更")
    t = run_event(ev, MockRuntime(), led, _project())
    assert t.phase == "waiting_human" and t.status == "paused" and t.session["ended"] is True
    done = [h["phase"] for h in t.history if h["status"] == "done"]
    assert done == list(PHASES)
    assert "ST-005" in t.cases["retired"] and "ST-013" in t.cases["added"]
    assert "ST-005" in t.impact
    t2 = approve(t.task, "tester", led)
    assert t2.status == "done" and t2.history[-1]["approver"] == "tester"
    with pytest.raises(ValueError):
        approve(t.task, "again", led)


def test_blocked_then_answer_resumes_from_same_phase(tmp_path):
    led = Ledger(tmp_path / "tasks")
    ev = nl.from_text("library-loan", "上限到達時の扱いを変更")
    t = run_event(ev, MockRuntime(blocked_at="test-design"), led, _project())
    assert (t.phase, t.status) == ("test-design", "blocked") and t.session["ended"] is True
    ran = [h["phase"] for h in t.history if h["status"] == "running"]
    assert ran[-1] == "test-design" and "test-implementation" not in ran
    t = answer(t.task, "誘導しない。拒否メッセージのみ", led, MockRuntime())
    assert t.status == "paused" and t.phase == "waiting_human"
    resumed = [h["phase"] for h in t.history if h["status"] == "running"]
    assert resumed.count("test-plan") == 1  # 上流をやり直していない
    assert resumed.count("test-design") == 2


def test_budget_stop_is_recorded(tmp_path):
    led = Ledger(tmp_path / "tasks")
    ev = nl.from_text("library-loan", "x")
    t = run_event(ev, MockRuntime(stopped_at="test-execution"), led, _project())
    assert (t.phase, t.status) == ("test-execution", "stopped") and t.reason == "not_converged"


def test_project_requires_env_md(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "config.toml").write_text("[project]\nname='p'\n")
    with pytest.raises(FileNotFoundError):
        load_project("p", tmp_path)


def test_cli_run_status_approve(ws, capsys):
    tasks = str(ws / "tasks")
    assert main(["--tasks", tasks, "--projects", str(ws / "projects"), "run", "--project", "library-loan", "--nl", "貸出上限を 3 冊に"]) == 0
    assert main(["--tasks", tasks, "status"]) == 0
    out = capsys.readouterr().out
    assert "T-0001" in out and "paused" in out
    assert main(["--tasks", tasks, "status", "T-0001", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["phase"] == "waiting_human"
    assert main(["--tasks", tasks, "approve", "T-0001", "--by", "qa"]) == 0
    assert "done" in capsys.readouterr().out


def test_web_api_serves_ledger(ws):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from qa_sentinel.web.app import make_handler

    led = Ledger(ws / "tasks")
    run_event(nl.from_text("library-loan", "x"), MockRuntime(), led, _project())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(led))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        port = srv.server_address[1]
        rows = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tasks").read())
        assert rows[0]["phase"] == "waiting_human"
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "qa-sentinel 台帳" in html
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/approve", data=b"task=T-0001&by=web", method="POST")
        assert json.loads(urllib.request.urlopen(req).read())["status"] == "done"
    finally:
        srv.shutdown()
