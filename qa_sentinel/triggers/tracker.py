"""トラッカー relay ポーラー — JIRA / Confluence / Redmine を決定論的に見張る（LLM は使わない）。

更新を検知したときだけ ChangeEvent を返す。常駐は人が systemd/launchd で行う（ここでは何も作らない）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from ..core.event import ChangeEvent, new_event_id
from ..core.project import Project
from . import nl

_DIRECT = "direct モードの担当（未割当（別タスク））"
_KINDS = ("jira", "confluence", "redmine")


def _jql_time(iso: str) -> str:
    """ISO 8601（UTC）→ JQL/CQL の "yyyy/MM/dd HH:mm"。解釈できなければそのまま。"""
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y/%m/%d %H:%M")
    except ValueError:
        return iso


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TrackerState:
    last_seen: str
    polls: int = 0
    events: int = 0
    sessions_started: int = 0

    @staticmethod
    def path(tasks_dir: str | Path, project: str) -> Path:
        return Path(tasks_dir) / f".tracker-{project}.json"

    @classmethod
    def load(cls, tasks_dir: str | Path, project: str) -> "TrackerState":
        try:
            d = json.loads(cls.path(tasks_dir, project).read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls(last_seen=_now())

    def save(self, tasks_dir: str | Path, project: str) -> None:
        p = self.path(tasks_dir, project)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")  # *.json.tmp → os.replace（Ledger.save と同じ）
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)


def _default_fetch(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _items(kind: str, base: str, data: dict) -> list[tuple[str, str, str, str]]:
    """(key, url, title, body) のリスト。"""
    out = []
    if kind == "jira":
        for i in data.get("issues", []):
            f = i.get("fields", {})
            out.append((str(i["key"]), f"{base}/browse/{i['key']}", f.get("summary") or "", f.get("description") or ""))
    elif kind == "confluence":
        for r in data.get("results", []):
            body = r.get("body", {}).get("storage", {}).get("value", "") if isinstance(r.get("body"), dict) else (r.get("body") or "")
            out.append((str(r["id"]), base + r.get("_links", {}).get("webui", ""), r.get("title") or "", body or ""))
    else:
        for i in data.get("issues", []):
            out.append((str(i["id"]), f"{base}/issues/{i['id']}", i.get("subject") or "", i.get("description") or ""))
    return out


def poll_once(project: Project, state: TrackerState, fetch=None) -> list[ChangeEvent]:
    cfg = project.config.get("triggers", {}).get("tracker", {})
    mode, kind = cfg.get("mode"), cfg.get("kind")
    base = str(cfg.get("base_url", "")).rstrip("/")
    query, cred = cfg.get("query", ""), str(cfg.get("credential_ref", ""))

    # 設定エラー: 入口で判定し、そのまま送出（fetch は呼ばない・polls も増やさない）
    if mode != "relay":
        raise NotImplementedError(_DIRECT)
    headers: dict = {}
    if cred.startswith("vault:"):
        raise NotImplementedError(_DIRECT)
    if cred.startswith("env:"):
        var = cred[4:]
        if var not in os.environ:
            raise RuntimeError(f"env var not set: {var}")
        headers["Authorization"] = f"Bearer {os.environ[var]}"
    else:
        raise ValueError(f"unknown credential_ref: {cred}")
    if kind not in _KINDS:
        raise ValueError(f"unknown tracker kind: {kind}")

    since = state.last_seen
    if kind == "jira":  # JQL の日時は "yyyy/MM/dd HH:mm"（ISO 8601 は 400）
        url = f"{base}/rest/api/2/search?jql=" + quote(f'{query} AND updated >= "{_jql_time(since)}"')
    elif kind == "confluence":  # CQL も同じ書式
        url = f"{base}/rest/api/content/search?cql=" + quote(f'lastmodified >= "{_jql_time(since)}"')
    else:
        url = f"{base}/issues.json?updated_on=" + quote(f">={since}", safe=">=:")

    state.polls += 1
    polled_at = _now()
    try:
        data = (fetch or _default_fetch)(url, headers)
        events = []
        for key, link, title, body in _items(kind, base, data):
            text = title + "\n\n" + body
            events.append(ChangeEvent(id=new_event_id(), project=project.name, source="tracker", text=text,
                                      changed_ids=nl.extract_ids(text), ref={"issue": key, "url": link}))
    except Exception as e:  # noqa: BLE001 — 通信/JSON/形の不正は飲み込む（落ちない）
        print(f"tracker poll failed: {type(e).__name__}: {e}", file=sys.stderr)
        return []
    state.last_seen = polled_at
    state.events += len(events)
    return events
