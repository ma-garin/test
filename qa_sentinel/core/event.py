"""ChangeEvent — すべてのトリガーが作る共通の入力。LLM を使わずに作る。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SOURCES: tuple[str, ...] = ("nl", "pr", "doc", "tracker", "approval")


@dataclass
class ChangeEvent:
    id: str
    project: str
    source: str
    text: str = ""
    changed_ids: list[str] = field(default_factory=list)
    diff: str | None = None
    ref: dict = field(default_factory=dict)
    resume_from: str | None = None
    received_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"unknown source: {self.source}")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeEvent":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def new_event_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return "evt_" + now.strftime("%Y%m%d_%H%M%S%f")[:-3]
