"""MVP の固定テスト。状態機械・台帳・Plan・司令塔（モック）・トリガー・CLI・Web。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from qa_sentinel.cli import main
from qa_sentinel.core.event import ChangeEvent
from qa_sentinel.core.ledger import Ledger, Task
from qa_sentinel.core.orchestrator import answer, approve, review, run_event, submit
from qa_sentinel.core.plan import PRESETS, Plan, draft_until
from qa_sentinel.core.project import load_project
from qa_sentinel.core.state import PHASES, next_phase, phases_from
from qa_sentinel.runtime.mock import MockRuntime
from qa_sentinel.triggers import nl

ROOT = Path(__file__).resolve().parents[1]
M1 = Plan.from_preset("M1", "qa", 5.0)


@pytest.fixture
def ws(tmp_path):
    shutil.copytree(ROOT / "projects", tmp_path / "projects")
    return tmp_path


def _project():
    return load_project("library-loan", ROOT / "projects")


def _run(led, text="貸出上限を 5 冊から 3 冊に変更", plan=M1, rt=None):
    return run_event(nl.from_text("library-loan", text), rt or MockRuntime(), led, _project(), plan=plan)


def test_phases_order_and_next():
    assert PHASES[0] == "test-plan" and PHASES[-1] == "regression-swap"
    assert next_phase("test-plan") == "test-analysis" and next_phase("regression-swap") is None
    assert phases_from("test-design")[0] == "test-design"
    with pytest.raises(ValueError):
        phases_from("nope")


def test_plan_presets_and_validation():
    assert set(PRESETS) == {f"M{i}" for i in range(1, 9)}
    p = Plan.from_preset("M2", "yuki", 5.0)
    assert p.drafter("test-execution") == "human" and p.drafter("defect-analysis") == "ai" and p.stops("test-design")
    assert draft_until("test-design") == list(PHASES[:4])
    with pytest.raises(ValueError):
        Plan(reviewer="")
    with pytest.raises(ValueError):
        Plan(reviewer="x", stop_at=["nope"])


def test_nl_trigger_extracts_ids():
    ev = nl.from_text("library-loan", "REQ-F-003 の上限を変更。DD-004 も")
    assert ev.source == "nl" and ev.changed_ids == ["DD-004", "REQ-F-003"]
    with pytest.raises(ValueError):
        nl.from_text("p", "   ")
    with pytest.raises(ValueError):
        ChangeEvent(id="e", project="p", source="slack")


def test_ledger_roundtrip(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = Task(task=led.new_id(), project="p", event="e")
    t.record("test-plan", "running")
    led.save(t)
    back = led.load(t.task)
    assert back.task == "T-0001" and back.history[-1]["phase"] == "test-plan" and back.decisions == []
    with pytest.raises(ValueError):
        t.record("test-plan", "weird")


def test_m1_walks_all_phases_stops_at_swap_then_approve(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = _run(led)
    # M1 は差し替えの出口で確定待ち
    assert (t.phase, t.status) == ("regression-swap", "review") and t.session["ended"] is True
    assert t.evidence["test-plan"] and t.branch == "qa-sentinel/T-0001"
    t = review(t.task, "yuki", True, led, MockRuntime(), note="ST-014 の期待結果を修正")
    assert (t.phase, t.status) == ("waiting_human", "paused")
    assert t.decisions[-1] == {**t.decisions[-1], "by": "yuki", "kind": "confirm", "phase": "regression-swap"}
    t = approve(t.task, "yuki", led)
    assert t.status == "done" and t.decisions[-1]["kind"] == "approve"
    with pytest.raises(ValueError):
        approve(t.task, "again", led)


def test_review_reject_redoes_same_phase(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = _run(led, plan=Plan.from_preset("M4", "yuki", 5.0))  # 設計まで AI、設計の出口で確定
    assert (t.phase, t.status) == ("test-design", "review")
    t = review(t.task, "yuki", False, led, MockRuntime(), note="境界値が足りない")
    assert (t.phase, t.status) == ("test-design", "review")
    ran = [h["phase"] for h in t.history if h["status"] == "running"]
    assert ran.count("test-design") == 2 and ran.count("test-plan") == 1
    t = review(t.task, "yuki", True, led, MockRuntime())
    # M4: 実装以降は人が下書き → handoff（セッションは立たない）
    assert (t.phase, t.status) == ("test-implementation", "handoff")


def test_m2_hands_execution_to_human_and_resumes(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = _run(led, plan=Plan.from_preset("M2", "yuki", 5.0))
    assert (t.phase, t.status) == ("test-design", "review")
    t = review(t.task, "yuki", True, led, MockRuntime())
    assert (t.phase, t.status) == ("test-execution", "handoff")
    t = submit(t.task, "yuki", led, MockRuntime(), artifact="results.csv", note="PASS 3 FAIL 1")
    assert t.artifacts["test-execution"] == "results.csv"
    assert (t.phase, t.status) == ("regression-swap", "review")
    with pytest.raises(ValueError):
        submit(t.task, "yuki", led, MockRuntime())


def test_blocked_then_answer_resumes_from_same_phase(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = _run(led, "上限到達時の扱いを変更", rt=MockRuntime(blocked_at="test-design"))
    assert (t.phase, t.status) == ("test-design", "blocked") and t.session["ended"] is True
    t = answer(t.task, "yuki", "誘導しない。拒否のみ", led, MockRuntime())
    assert (t.phase, t.status) == ("regression-swap", "review")
    assert t.decisions[0]["kind"] == "answer" and t.decisions[0]["by"] == "yuki"
    with pytest.raises(ValueError):
        answer(t.task, "", "x", led, MockRuntime())


def test_budget_stop_is_recorded(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = _run(led, "x", rt=MockRuntime(stopped_at="test-execution"))
    assert (t.phase, t.status) == ("test-execution", "stopped") and t.reason == "not_converged"


def test_m7_human_drafts_first_phase_without_session(tmp_path):
    led = Ledger(tmp_path / "tasks")
    t = _run(led, plan=Plan.from_preset("M7", "yuki", 5.0))
    assert (t.phase, t.status) == ("test-plan", "handoff") and t.session["id"] is None


def test_project_requires_env_md(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "config.toml").write_text("[project]\nname='p'\n")
    with pytest.raises(FileNotFoundError):
        load_project("p", tmp_path)


def test_cli_run_with_preset_status_review_approve(ws, capsys):
    base = ["--tasks", str(ws / "tasks"), "--projects", str(ws / "projects")]
    assert main(base + ["run", "--project", "library-loan", "--nl", "貸出上限を 3 冊に", "--mode", "M1", "--reviewer", "qa", "--budget", "3"]) == 0
    out = capsys.readouterr().out
    assert "mode M1" in out and "確定待ち" in out
    assert main(base + ["show", "T-0001", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["plan"]["reviewer"] == "qa" and d["plan"]["budget_usd"] == 3.0
    assert main(base + ["ok", "T-0001", "--by", "qa", "-m", "確認した"]) == 0
    assert "承認待ち" in capsys.readouterr().out
    assert main(base + ["approve", "T-0001", "--by", "qa"]) == 0
    assert "done" in capsys.readouterr().out
    assert main(base + ["approve", "T-0001", "--by", "qa"]) == 2  # 二重承認はエラー終了


def test_cli_four_questions_defaults_when_not_tty(ws, capsys):
    base = ["--tasks", str(ws / "tasks"), "--projects", str(ws / "projects")]
    # TTY でない → 既定値（全部 AI、差し替えで止まる、reviewer は --reviewer）
    assert main(base + ["run", "--project", "library-loan", "--nl", "x", "--reviewer", "qa"]) == 0
    assert "custom" in capsys.readouterr().out
    assert (ws / "tasks" / ".last-plan.json").exists()


def test_web_api_review_submit_answer_approve(ws):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from qa_sentinel.web.app import make_handler

    led = Ledger(ws / "tasks")
    _run(led, plan=Plan.from_preset("M2", "yuki", 5.0))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(led, MockRuntime()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        u = f"http://127.0.0.1:{srv.server_address[1]}"
        post = lambda path, data: json.loads(urllib.request.urlopen(urllib.request.Request(u + path, data=data.encode(), method="POST")).read())
        assert json.loads(urllib.request.urlopen(u + "/api/tasks").read())[0]["status"] == "review"
        assert "qa-sentinel 台帳" in urllib.request.urlopen(u + "/").read().decode()
        assert post("/api/review", "task=T-0001&by=yuki&ok=1")["status"] == "handoff"
        assert post("/api/submit", "task=T-0001&by=yuki&file=r.csv")["status"] == "review"
        assert post("/api/review", "task=T-0001&by=yuki&ok=1")["status"] == "paused"
        assert post("/api/approve", "task=T-0001&by=yuki")["status"] == "done"
        try:
            urllib.request.urlopen(urllib.request.Request(u + "/api/approve", data=b"task=T-0001&by=yuki", method="POST"))
            assert False, "二重承認は 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()


def test_web_api_run_starts_task(ws):
    import threading
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer

    from qa_sentinel.web.app import make_handler

    led = Ledger(ws / "tasks")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(led, MockRuntime(), projects_dir=str(ws / "projects")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        u = f"http://127.0.0.1:{srv.server_address[1]}"
        body = urllib.parse.urlencode({"project": "library-loan", "nl": "貸出上限を 3 冊に", "mode": "M2", "reviewer": "yuki", "budget": "3"}).encode()
        d = json.loads(urllib.request.urlopen(urllib.request.Request(u + "/api/run", data=body, method="POST")).read())
        assert d["task"] == "T-0001" and d["status"] == "review" and d["plan"]["reviewer"] == "yuki" and d["plan"]["budget_usd"] == 3.0
        try:
            urllib.request.urlopen(urllib.request.Request(u + "/api/run", data=b"project=library-loan&nl=x&mode=M2&reviewer=", method="POST"))
            assert False, "名前なしは 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()


def test_menu_runs_when_no_subcommand(ws, monkeypatch, capsys):
    answers = iter(["1", "library-loan", "貸出上限を 3 冊に", "M1", "qa", "2", "0"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    assert main(["--tasks", str(ws / "tasks"), "--projects", str(ws / "projects")]) == 0
    out = capsys.readouterr().out
    assert "あなたの番" in out and "T-0001" in out
