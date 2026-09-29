"""トラッカー relay ポーラーの固定テスト。fetch を注入し、実ネットワークへは出ない。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from qa_sentinel import cli
from qa_sentinel.core.event import ChangeEvent
from qa_sentinel.core.project import Project
from qa_sentinel.triggers import nl, tracker

ROOT = Path(__file__).resolve().parents[1]


def _project(kind="jira", cred="env:JIRA_TOKEN", mode="relay"):
    cfg = {"triggers": {"tracker": {"mode": mode, "kind": kind, "base_url": "https://jira.example.com/",
                                    "query": "project = PROJ", "poll_minutes": 1, "credential_ref": cred}}}
    return Project(name="library-loan", root=ROOT, config=cfg, env_md="")


@pytest.fixture(autouse=True)
def _tok(monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "secret")


def test_zero_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    st = tracker.TrackerState(last_seen="2026-01-01T00:00:00+00:00")
    assert tracker.poll_once(_project(), st, fetch=lambda u, h: {"issues": []}) == []
    assert st.polls == 1 and st.sessions_started == 0 and st.events == 0
    assert st.last_seen != "2026-01-01T00:00:00+00:00"
    assert not list(tmp_path.glob("tasks/T-*.json"))


def test_one_event_and_cli(tmp_path, monkeypatch, capsys):
    seen = {}

    def fetch(url, headers):
        seen.update(url=url, headers=headers)
        return {"issues": [{"key": "PROJ-1", "fields": {"summary": "REQ-F-003 変更", "description": "貸出上限を 3 冊に"}}]}

    st = tracker.TrackerState(last_seen="2026-01-01T00:00:00+00:00")
    evs = tracker.poll_once(_project(), st, fetch=fetch)
    assert len(evs) == 1 and st.events == 1
    ev = evs[0]
    assert ev.source == "tracker" and ev.text == "REQ-F-003 変更\n\n貸出上限を 3 冊に"
    assert ev.changed_ids == nl.extract_ids(ev.text)
    assert ev.ref == {"issue": "PROJ-1", "url": "https://jira.example.com/browse/PROJ-1"}
    assert seen["headers"]["Authorization"] == "Bearer secret" and "/rest/api/2/search?jql=" in seen["url"]

    shutil.copytree(ROOT / "projects", tmp_path / "projects")
    monkeypatch.setattr(tracker, "poll_once", lambda p, s, fetch=None: evs)
    calls = []
    monkeypatch.setattr(cli, "run_event", lambda e, rt, led, prj, plan=None: calls.append(e) or type("T", (), {
        "task": "T-1", "project": "p", "mode": "M1", "phase": "x", "status": "review", "reason": "", "session": {}})())
    rc = cli.main(["--projects", str(tmp_path / "projects"), "--tasks", str(tmp_path / "tasks"),
                   "watch", "--project", "library-loan", "--once", "--mode", "M1", "--reviewer", "yuki"])
    assert rc == 0 and calls == evs
    assert tracker.TrackerState.load(tmp_path / "tasks", "library-loan").sessions_started == 1


def test_fetch_error_swallowed(capsys):
    def boom(url, headers):
        raise OSError("down")

    st = tracker.TrackerState(last_seen="2026-01-01T00:00:00+00:00")
    assert tracker.poll_once(_project(), st, fetch=boom) == []
    assert st.last_seen == "2026-01-01T00:00:00+00:00" and st.polls == 1
    assert "down" in capsys.readouterr().err


def test_watch_once(tmp_path, monkeypatch, capsys):
    shutil.copytree(ROOT / "projects", tmp_path / "projects")
    monkeypatch.setattr(tracker, "poll_once", lambda p, s, fetch=None: [])
    rc = cli.main(["--projects", str(tmp_path / "projects"), "--tasks", str(tmp_path / "tasks"),
                   "watch", "--project", "library-loan", "--once", "--mode", "M1", "--reviewer", "yuki"])
    assert rc == 0 and "polled: 0 events" in capsys.readouterr().out
    assert not list((tmp_path / "tasks").glob("T-*.json"))


def test_config_errors_do_not_fetch():
    st = tracker.TrackerState(last_seen="x")
    f = lambda u, h: pytest.fail("fetch called")  # noqa: E731
    for prj, exc in ((_project(mode="direct"), NotImplementedError), (_project(cred="vault:a"), NotImplementedError),
                     (_project(cred="file:a"), ValueError), (_project(kind="x"), ValueError)):
        with pytest.raises(exc):
            tracker.poll_once(prj, st, fetch=f)
    with pytest.raises(RuntimeError):
        import os
        os.environ.pop("JIRA_TOKEN")
        tracker.poll_once(_project(), st, fetch=f)
    assert st.polls == 0
