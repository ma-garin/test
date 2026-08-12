"""AH-01: 機械可読な作業キュー。

実行ログ（`docs/改善に.md` の表）は人が読むための記録であり、順序や依存を機械が
たどれない。ここでは同じ内容を `docs/agent/queue.json` に持ち、別セッションの
エージェントが「未完了の最優先チケット」と「直前の失敗証跡」を 1 か所で取得できる
ようにする。

不変条件:
- チケットは書き換えず、`replace` で新しい値を作る。
- 依存に循環がある、または存在しない ID を指す状態は読み込み時に拒否する。
- `done` にできるのは証跡（evidence）が 1 件以上あるときだけ。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = REPO_ROOT / "docs" / "agent" / "queue.json"

VALID_STATES = ("untouched", "in_progress", "done", "hold", "decision_required")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
OPEN_STATES = ("untouched", "in_progress")


class QueueError(ValueError):
    """キューの内容が不変条件を満たさないときに送出する。"""


def _is_rejected(review: dict | None) -> bool:
    """AH-04: レビューで 1 項目でも落ちていれば完了にできない。"""

    return bool(review) and bool(review.get("failed"))


@dataclass(frozen=True)
class Ticket:
    """1 チケット。値オブジェクトとして扱い、更新は新しい実体を返す。"""

    id: str
    priority: str
    kind: str
    state: str
    acceptance: str
    depends_on: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    hold_reason: str | None = None
    note: str | None = None
    #: AH-04 のレビュー結果。`done` にするにはここが承認済みである必要がある。
    review: dict | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> Ticket:
        missing = {"id", "priority", "kind", "state", "acceptance"} - set(raw)
        if missing:
            raise QueueError(f"チケットに必須項目がありません: {sorted(missing)}")
        if raw["state"] not in VALID_STATES:
            raise QueueError(f"{raw['id']}: 未知の state '{raw['state']}'")
        if raw["priority"] not in PRIORITY_ORDER:
            raise QueueError(f"{raw['id']}: 未知の priority '{raw['priority']}'")
        return cls(
            id=raw["id"],
            priority=raw["priority"],
            kind=raw["kind"],
            state=raw["state"],
            acceptance=raw["acceptance"],
            depends_on=tuple(raw.get("depends_on", ())),
            evidence=tuple(raw.get("evidence", ())),
            hold_reason=raw.get("hold_reason"),
            note=raw.get("note"),
            review=raw.get("review"),
        )

    def to_dict(self) -> dict:
        data: dict = {
            "id": self.id,
            "priority": self.priority,
            "kind": self.kind,
            "depends_on": list(self.depends_on),
            "state": self.state,
            "acceptance": self.acceptance,
        }
        if self.evidence:
            data["evidence"] = list(self.evidence)
        if self.hold_reason:
            data["hold_reason"] = self.hold_reason
        if self.note:
            data["note"] = self.note
        if self.review:
            data["review"] = self.review
        return data

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES


@dataclass(frozen=True)
class TicketQueue:
    """チケット集合。読み込み時に依存の健全性を検証する。"""

    tickets: tuple[Ticket, ...]
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> TicketQueue:
        target = path or QUEUE_PATH
        raw = json.loads(target.read_text(encoding="utf-8"))
        tickets = tuple(Ticket.from_dict(item) for item in raw.get("tickets", ()))
        meta = {k: v for k, v in raw.items() if k != "tickets"}
        queue = cls(tickets=tickets, meta=meta)
        queue.validate()
        return queue

    def save(self, path: Path | None = None) -> None:
        target = path or QUEUE_PATH
        payload = dict(self.meta)
        payload["tickets"] = [t.to_dict() for t in self.tickets]
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def validate(self) -> None:
        known = {t.id for t in self.tickets}
        if len(known) != len(self.tickets):
            raise QueueError("チケット ID が重複しています。")
        for ticket in self.tickets:
            unknown = set(ticket.depends_on) - known
            if unknown:
                raise QueueError(f"{ticket.id}: 未知の依存 {sorted(unknown)}")
            if ticket.state == "done" and not ticket.evidence:
                raise QueueError(f"{ticket.id}: 証跡なしで done にはできません。")
            if ticket.state == "done" and _is_rejected(ticket.review):
                raise QueueError(f"{ticket.id}: レビューで差し戻された項目が残っています。")
            if ticket.state == "hold" and not ticket.hold_reason:
                raise QueueError(f"{ticket.id}: hold には理由が必要です。")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        edges = {t.id: tuple(t.depends_on) for t in self.tickets}
        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(node: str, path: tuple[str, ...]) -> None:
            if node in visited:
                return
            if node in visiting:
                cycle = " → ".join((*path, node))
                raise QueueError(f"チケット依存に循環があります: {cycle}")
            visiting.add(node)
            for nxt in edges.get(node, ()):
                walk(nxt, (*path, node))
            visiting.discard(node)
            visited.add(node)

        for ticket_id in edges:
            walk(ticket_id, ())

    def get(self, ticket_id: str) -> Ticket:
        for ticket in self.tickets:
            if ticket.id == ticket_id:
                return ticket
        raise QueueError(f"チケットがありません: {ticket_id}")

    def is_unblocked(self, ticket: Ticket) -> bool:
        return all(self.get(dep).state == "done" for dep in ticket.depends_on)

    def ready(self) -> tuple[Ticket, ...]:
        """依存解除済みで未完了のチケットを、優先度→ID 順に返す。"""
        candidates = [t for t in self.tickets if t.is_open and self.is_unblocked(t)]
        candidates.sort(key=lambda t: (PRIORITY_ORDER[t.priority], t.id))
        return tuple(candidates)

    def next_ticket(self) -> Ticket | None:
        """ループの SELECT。進行中があればそれを、なければ最優先の未着手を返す。"""
        ready = self.ready()
        in_progress = [t for t in ready if t.state == "in_progress"]
        if in_progress:
            return in_progress[0]
        return ready[0] if ready else None

    def blocked(self) -> tuple[Ticket, ...]:
        return tuple(t for t in self.tickets if t.is_open and not self.is_unblocked(t))

    def with_ticket(self, ticket: Ticket) -> TicketQueue:
        """1 件を差し替えた新しいキューを返す（元のキューは変更しない）。"""
        if not any(t.id == ticket.id for t in self.tickets):
            raise QueueError(f"チケットがありません: {ticket.id}")
        updated = tuple(ticket if t.id == ticket.id else t for t in self.tickets)
        new_queue = TicketQueue(tickets=updated, meta=dict(self.meta))
        new_queue.validate()
        return new_queue

    def update(
        self,
        ticket_id: str,
        *,
        state: str | None = None,
        evidence: Iterable[str] | None = None,
        hold_reason: str | None = None,
        note: str | None = None,
        review: dict | None = None,
    ) -> TicketQueue:
        current = self.get(ticket_id)
        changes: dict = {}
        if review is not None:
            changes["review"] = review
        if state is not None:
            if state not in VALID_STATES:
                raise QueueError(f"未知の state '{state}'")
            changes["state"] = state
        if evidence is not None:
            merged = (*current.evidence, *(e for e in evidence if e not in current.evidence))
            changes["evidence"] = merged
        if hold_reason is not None:
            changes["hold_reason"] = hold_reason
        if note is not None:
            changes["note"] = note
        return self.with_ticket(replace(current, **changes))

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = dict.fromkeys(VALID_STATES, 0)
        for ticket in self.tickets:
            counts[ticket.state] += 1
        return counts
