"""成果物一覧の表示用データ。

赤字率と承認可否は 1 行ごとに同じ計算を繰り返すため、テンプレートで条件分岐を
散らかさずに済むよう、行オブジェクトへ畳んでから画面へ渡す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from apps.pmo.models import Deliverable
from apps.pmo.services.approval import blocking_reason

#: PoC の受け入れ条件。赤字率がこの値未満なら「良好」とみなす。
CORRECTION_RATE_TARGET_PERCENT = 20


@dataclass(frozen=True)
class DeliverableRow:
    """成果物 1 件の表示単位。"""

    deliverable: Deliverable
    correction_percent: int | None
    blocking_reason: str

    @property
    def can_approve(self) -> bool:
        return not self.blocking_reason

    @property
    def tone(self) -> str:
        """赤字率の色分け。g=目標内 / a=超過 / n=AI未使用で算出不能。"""

        if self.correction_percent is None:
            return "n"

        return "g" if self.correction_percent < CORRECTION_RATE_TARGET_PERCENT else "a"

    @property
    def status_tone(self) -> str:
        return {
            Deliverable.Status.APPROVED: "g",
            Deliverable.Status.PENDING_APPROVAL: "b",
            Deliverable.Status.REJECTED: "r",
        }.get(self.deliverable.status, "n")


@dataclass
class DeliverableReport:
    """一覧とその集計。"""

    rows: list[DeliverableRow] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def blocked_count(self) -> int:
        return sum(1 for row in self.rows if not row.can_approve)

    @property
    def approved_count(self) -> int:
        return sum(1 for row in self.rows if row.deliverable.status == Deliverable.Status.APPROVED)

    @property
    def measured_rows(self) -> list[DeliverableRow]:
        return [row for row in self.rows if row.correction_percent is not None]

    @property
    def average_correction_percent(self) -> int:
        """AI 生成本文がある成果物の平均赤字率。無ければ 0。"""

        measured = self.measured_rows

        if not measured:
            return 0

        return round(sum(row.correction_percent for row in measured) / len(measured))

    @property
    def correction_tone(self) -> str:
        return "g" if self.average_correction_percent < CORRECTION_RATE_TARGET_PERCENT else "a"


def build_report(deliverables: Iterable[Deliverable]) -> DeliverableReport:
    """成果物のイテラブルから表示用レポートを組み立てる。"""

    return DeliverableReport(rows=[_build_row(item) for item in deliverables])


def _build_row(deliverable: Deliverable) -> DeliverableRow:
    rate = deliverable.correction_rate

    return DeliverableRow(
        deliverable=deliverable,
        correction_percent=None if rate is None else round(rate * 100),
        blocking_reason=blocking_reason(deliverable),
    )
