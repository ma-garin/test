"""④ 自然言語トリガー。文字列から ChangeEvent を作る。LLM を使わない。

changed_ids は本文中の ID 表記（REQ-F-003 など）を正規表現で拾うだけ。拾えなければ空にして test-plan で補完させる。
"""
from __future__ import annotations

import re

from ..core.event import ChangeEvent, new_event_id

_ID = re.compile(r"\b(?:RFD|REQ-[FN]|BD|DD|T|UT|IT|ST|UAT|OPS|DEF)-\d{3}\b")


def extract_ids(text: str) -> list[str]:
    return sorted(set(_ID.findall(text)))


def from_text(project: str, text: str) -> ChangeEvent:
    if not text.strip():
        raise ValueError("指示文が空")
    return ChangeEvent(id=new_event_id(), project=project, source="nl", text=text.strip(), changed_ids=extract_ids(text))
