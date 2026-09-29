from qa_sentinel.core.event import ChangeEvent
from qa_sentinel.triggers import pr
from qa_sentinel.triggers.nl import extract_ids


def _fetcher(body, files):
    def fetch(url):
        if url.endswith("/files"):
            return files
        return {"title": "T", "body": body, "html_url": "https://github.com/o/r/pull/7"}
    return fetch


def test_relation_id_section_only():
    body = "概要 REQ-F-001 に関連\n\n## 関係 ID\n- REQ-F-003\n\n## 備考\nDD-009 も見た\n"
    ev = pr.from_github("p", "o/r", 7, fetch=_fetcher(body, [{"filename": "a.py", "patch": "+x"}]))
    assert ev.changed_ids == ["REQ-F-003"]
    assert ev.source == "pr" and ev.ref["files"] == ["a.py"] and ev.ref["pr"] == 7


def test_fallback_whole_body():
    body = "# 概要\nREQ-F-001 と UT-002 を変更\n## 備考\nDD-009\n"
    ev = pr.from_github("p", "o/r", 7, fetch=_fetcher(body, []))
    assert ev.changed_ids == extract_ids(body) == ["DD-009", "REQ-F-001", "UT-002"]


def test_diff_truncated():
    files = [{"filename": f"f{i}.py", "patch": "a" * 100_000} for i in range(3)] + [{"filename": "n.bin"}]
    ev = pr.from_github("p", "o/r", 7, fetch=_fetcher(None, files))
    assert ev.diff.endswith("[truncated]")
    assert len(ev.diff.encode("utf-8")) == 200 * 1024 + len("[truncated]")
    assert ev.ref["files"][-1] == "n.bin"


def test_webhook_action_filter():
    payload = {"action": "closed", "pull_request": {"number": 1, "title": "t", "body": "REQ-F-002", "html_url": "u"}}
    assert pr.from_webhook("p", payload) is None
    payload["action"] = "opened"
    ev = pr.from_webhook("p", payload)
    assert isinstance(ev, ChangeEvent) and ev.diff is None
    assert ev.changed_ids == ["REQ-F-002"] and ev.ref == {"pr": 1, "url": "u"}
