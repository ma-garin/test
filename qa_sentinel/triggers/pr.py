"""③ PR トリガー。PR 本文の「関係 ID」欄と diff から ChangeEvent を作る。LLM を使わない。

本文・diff はデータとして格納するだけ（指示形の文があっても何もしない）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from ..core.event import ChangeEvent, new_event_id
from .nl import extract_ids

MAX_DIFF_BYTES = 200 * 1024
_ACTIONS = ("opened", "synchronize", "reopened")
_HEADING = re.compile(r"^#")
_SETEXT = re.compile(r"^(?:-{3,}|={3,})\s*$")


def _default_fetch(url: str):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def _is_heading(lines: list[str], i: int) -> bool:
    if _HEADING.match(lines[i]):
        return True
    return i + 1 < len(lines) and bool(lines[i].strip()) and bool(_SETEXT.match(lines[i + 1])) and not _SETEXT.match(lines[i])


def _ids_from_body(body: str) -> list[str]:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if "関係 ID" in line and _is_heading(lines, i):
            start = i + 2 if (i + 1 < len(lines) and _SETEXT.match(lines[i + 1])) else i + 1
            end = len(lines)
            for j in range(start, len(lines)):
                if _is_heading(lines, j):
                    end = j
                    break
            return extract_ids("\n".join(lines[start:end]))
    return extract_ids(body)


def _cap(diff: str) -> str:
    raw = diff.encode("utf-8")
    if len(raw) <= MAX_DIFF_BYTES:
        return diff
    return raw[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore") + "[truncated]"


def from_github(project: str, repo: str, number: int, fetch=None) -> ChangeEvent:
    fetch = fetch or _default_fetch
    base = f"https://api.github.com/repos/{repo}/pulls/{number}"
    pr = fetch(base)
    files = fetch(f"{base}/files")
    body = pr.get("body") or ""
    diff = _cap("\n".join(f["patch"] for f in files if f.get("patch")))
    return ChangeEvent(
        id=new_event_id(), project=project, source="pr",
        text=(pr.get("title") or "") + "\n\n" + body,
        changed_ids=_ids_from_body(body), diff=diff,
        ref={"pr": number, "url": pr.get("html_url"), "files": [f.get("filename") for f in files]},
    )


def from_webhook(project: str, payload: dict) -> ChangeEvent | None:
    if payload.get("action") not in _ACTIONS:
        return None
    pr = payload["pull_request"]
    body = pr.get("body") or ""
    return ChangeEvent(
        id=new_event_id(), project=project, source="pr",
        text=(pr.get("title") or "") + "\n\n" + body,
        changed_ids=_ids_from_body(body), diff=None,
        ref={"pr": pr["number"], "url": pr.get("html_url")},
    )
