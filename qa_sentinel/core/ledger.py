"""台帳 tasks/<id>.json — 真実源。CLI と Web はこれだけを読む。セッションには問い合わせない。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .state import PHASES, STATUSES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    task: str
    project: str
    event: str
    phase: str = "test-plan"
    status: str = "queued"
    reason: str | None = None
    session: dict = field(default_factory=lambda: {"id": None, "budget_usd": 0.0, "spent_usd": 0.0, "ended": True})
    impact: list[str] = field(default_factory=list)
    cases: dict = field(default_factory=lambda: {"added": [], "retired": []})
    artifacts: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    plan: dict = field(default_factory=dict)          # 4 問の答え（core.plan.Plan）
    mode: str = "custom"
    branch: str = ""                                    # AI が書き込む作業ブランチ
    evidence: dict = field(default_factory=dict)      # 段 → 根拠の一覧
    decisions: list[dict] = field(default_factory=list)  # 人の確定・回答・承認（名前つき）
    nl: str = ""                                        # 変更の一文（画面の見出し）
    draft: dict = field(default_factory=dict)         # 段 → 下書きの中身（ケース表・手順・文）。無ければ artifacts のパスを見る
    session_count: int = 0                              # runtime.start() を呼んだ回数（L7 の上限判定）
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def record(self, phase: str, status: str, reason: str | None = None, **extra) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        if phase not in PHASES and phase not in ("waiting_human", "done"):
            raise ValueError(f"unknown phase: {phase}")
        self.phase, self.status, self.reason = phase, status, reason
        self.updated_at = _now()
        self.history.append({"at": self.updated_at, "phase": phase, "status": status, "reason": reason, **extra})

    def to_dict(self) -> dict:
        return asdict(self)


class Ledger:
    def __init__(self, root: str | os.PathLike = "tasks"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"

    def new_id(self) -> str:
        n = len(list(self.root.glob("T-*.json"))) + 1
        while self._path(f"T-{n:04d}").exists():
            n += 1
        return f"T-{n:04d}"

    def save(self, t: Task) -> Path:
        p = self._path(t.task)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(t.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)  # 途中で落ちても壊れた JSON を残さない
        return p

    def load(self, task_id: str) -> Task:
        d = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        return Task(**{k: v for k, v in d.items() if k in Task.__dataclass_fields__})

    def list(self) -> list[Task]:
        return sorted((self.load(p.stem) for p in self.root.glob("T-*.json")), key=lambda t: t.task)
