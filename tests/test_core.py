"""MVP の固定テスト。状態機械・台帳・Plan・司令塔（モック）・トリガー・CLI・Web。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from qa_sentinel.cli import main
from qa_sentinel.core.event import ChangeEvent
from qa_sentinel.core.ledger import Ledger, Task
from qa_sentinel.core.orchestrator import check_limits, answer, approve, review, run_event, submit
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


def test_web_static_guide_and_demo(ws):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from qa_sentinel.web.app import make_handler

    led = Ledger(ws / "tasks")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(led, MockRuntime(), projects_dir=str(ws / "projects")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        u = f"http://127.0.0.1:{srv.server_address[1]}"
        get = lambda path: urllib.request.urlopen(u + path)
        assert "window.PV" in get("/static/progress-views.js").read().decode()
        assert get("/static/progress-views.js").headers["Content-Type"].startswith("text/javascript")
        assert "図書館デモを動かす" in get("/").read().decode()
        assert "この型にする" in get("/guide").read().decode()
        for bad in ("/static/../pyproject.toml", "/static/nope.js", "/static/app.py"):
            try:
                get(bad)
                assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 404
        d = json.loads(urllib.request.urlopen(urllib.request.Request(u + "/api/demo", data=b"by=qa", method="POST")).read())
        assert d["project"] == "library-loan" and d["status"] == "review" and d["plan"]["reviewer"] == "qa" and d["plan"]["mode"] == "M2"
    finally:
        srv.shutdown()


def test_resume_uses_projects_dir_from_other_cwd(ws, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"; other.mkdir(); monkeypatch.chdir(other)
    assert main(["--tasks", str(ws / "tasks"), "--projects", str(ws / "projects"), "demo", "--by", "qa"]) == 0
    assert main(["--tasks", str(ws / "tasks"), "--projects", str(ws / "projects"), "ok", "T-0001", "--by", "qa"]) == 0
    assert Ledger(ws / "tasks").load("T-0001").status == "handoff"


def test_cli_demo_and_menu_item_5(ws, capsys, monkeypatch):
    assert main(["--tasks", str(ws / "tasks"), "--projects", str(ws / "projects"), "demo", "--by", "qa"]) == 0
    out = capsys.readouterr().out
    assert "T-0001" in out and "qa-sentinel ok T-0001 --by qa" in out
    answers = iter(["5", "qa", "0"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    assert main(["--tasks", str(ws / "tasks"), "--projects", str(ws / "projects")]) == 0
    assert "T-0002" in capsys.readouterr().out


def test_limits_reject_3_times_stops(tmp_path):
    led = Ledger(tmp_path / "tasks"); rt = MockRuntime()
    t = _run(led, plan=Plan.from_preset("M4", "yuki", 5.0), rt=rt)
    assert (t.phase, t.status) == ("test-design", "review")
    for i in range(3):
        t = review(t.task, "yuki", False, led, rt, note=f"r{i}")
    assert t.status == "stopped" and t.reason.startswith("rejects_exceeded:test-design")


def test_limits_answer_5_times_stops(tmp_path):
    led = Ledger(tmp_path / "tasks"); rt = MockRuntime(blocked_at="test-design")
    t = _run(led, plan=M1, rt=rt)
    assert t.status == "blocked"
    for i in range(5):
        t = answer(t.task, "yuki", f"a{i}", led, rt)
    assert t.status == "stopped" and t.reason.startswith("questions_exceeded")


def test_limits_session_count_10_stops_before_start(tmp_path):
    led = Ledger(tmp_path / "tasks"); rt = MockRuntime()
    task = Task(task=led.new_id(), project="library-loan", event="e", plan=M1.to_dict(), session_count=10)
    led.save(task)
    t = run_event(nl.from_text("library-loan", "x"), rt, led, _project(), task=task)
    assert t.status == "stopped" and t.reason.startswith("sessions_exceeded") and rt._n == 0


def test_limits_do_not_touch_normal_flow(tmp_path):
    led = Ledger(tmp_path / "tasks"); rt = MockRuntime()
    t = _run(led, plan=M1, rt=rt)
    t = review(t.task, "yuki", True, led, rt)
    t = approve(t.task, "yuki", led)
    assert t.status == "done" and t.session_count == 1 and check_limits(t) is None
    assert any(h.get("session") for h in t.history)


def test_approve_gate_missing_script_passes_and_ng_blocks(tmp_path, monkeypatch):
    from qa_sentinel.core import gates
    led = Ledger(tmp_path / "tasks"); rt = MockRuntime()
    t = _run(led, plan=M1, rt=rt); t = review(t.task, "yuki", True, led, rt)
    from qa_sentinel.core import project as projmod
    real = projmod.load_project
    def with_gate(name, d="projects"):
        p = real(name, d); p.config["project"]["approval_gate"] = "check-approval"; return p
    monkeypatch.setattr("qa_sentinel.core.orchestrator.load_project", with_gate)
    monkeypatch.setattr(gates, "run_gate", lambda *a, **k: gates.GateResult("check-approval", 1, False, "approver 未記入"))
    with pytest.raises(ValueError, match="check-approval NG"):
        approve(t.task, "yuki", led, projects_dir=str(Path(__file__).parent.parent / "projects"))
    assert led.load(t.task).status == "paused"
    monkeypatch.setattr(gates, "run_gate", lambda *a, **k: gates.GateResult("check-approval", 127, False, "missing", skipped=True))
    t = approve(t.task, "yuki", led, projects_dir=str(Path(__file__).parent.parent / "projects"))
    assert t.status == "done" and t.decisions[-1]["gate"] == "missing"
    t2 = _run(led, plan=M1, rt=rt); t2 = review(t2.task, "yuki", True, led, rt)
    assert approve(t2.task, "yuki", led, gate=False).decisions[-1].get("gate") is None
    monkeypatch.setattr("qa_sentinel.core.orchestrator.load_project", real)
    t3 = _run(led, plan=M1, rt=rt); t3 = review(t3.task, "yuki", True, led, rt)
    assert approve(t3.task, "yuki", led, projects_dir=str(Path(__file__).parent.parent / "projects")).decisions[-1]["gate"] == "skipped(config)"


def test_web_diff_endpoint_never_raises(ws):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from qa_sentinel.web.app import make_handler

    led = Ledger(ws / "tasks"); _run(led)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(led, MockRuntime(), projects_dir=str(ws / "projects")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        u = f"http://127.0.0.1:{srv.server_address[1]}"
        d = json.loads(urllib.request.urlopen(u + "/api/tasks/T-0001/diff").read())
        assert set(d) == {"stat", "diff"}
        try:
            urllib.request.urlopen(u + "/api/tasks/T-9999/diff"); assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_swap_applied_at_regression_swap_when_enabled(tmp_path):
    import shutil as _sh
    root = Path(__file__).parent.parent
    work = tmp_path / "work"; _sh.copytree(root / "demo" / "library-loan", work / "demo" / "library-loan")
    _sh.copytree(root / "projects", work / "projects")
    cfg = work / "projects" / "library-loan" / "config.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("apply_swap = false", "apply_swap = true"), encoding="utf-8")
    led = Ledger(work / "tasks"); rt = MockRuntime()
    import os
    old = os.getcwd(); os.chdir(work)
    try:
        pr = load_project("library-loan", work / "projects")
        t = run_event(nl.from_text("library-loan", "貸出上限を 5 冊から 3 冊に変更 REQ-F-003"), rt, led, pr, plan=Plan.from_preset("M1", "yuki", 5.0))
    finally:
        os.chdir(old)
    assert t.phase == "regression-swap" and any(e.startswith("swap: ok") for e in t.evidence["regression-swap"])
    csv_text = (work / "demo" / "library-loan" / "docs" / "test" / "system_test_cases.csv").read_text(encoding="utf-8")
    assert "失効(" in csv_text and len(t.cases["added"]) >= 3


def test_swap_failure_blocks_instead_of_done(tmp_path, monkeypatch):
    from qa_sentinel.core import project as projmod
    real = projmod.load_project
    pr = real("library-loan", str(Path(__file__).parent.parent / "projects"))
    pr.config["project"]["apply_swap"] = True
    monkeypatch.setattr("qa_sentinel.core.swap.swap", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("csv")))
    led = Ledger(tmp_path / "tasks"); rt = MockRuntime()
    t = run_event(nl.from_text("library-loan", "x"), rt, led, pr, plan=M1)
    assert (t.phase, t.status) == ("regression-swap", "blocked") and t.reason.startswith("swap_error") and t.session["ended"]


def test_web_rejects_bad_task_ids(ws):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from qa_sentinel.web.app import make_handler

    led = Ledger(ws / "tasks"); _run(led)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(led, MockRuntime()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        u = f"http://127.0.0.1:{srv.server_address[1]}"
        for bad in ("/api/tasks/..%2F..%2Fx", "/api/tasks/T-0001/other", "/api/tasks/abc", "/api/tasks/T-0001/diff/x"):
            try:
                urllib.request.urlopen(u + bad); assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 404, bad
    finally:
        srv.shutdown()
