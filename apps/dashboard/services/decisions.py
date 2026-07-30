"""人の判断が要る一覧（リスク・変更要求・AI介入提案）の集計。

3 つとも「一覧を出し、状態別の件数を添え、色で優先順位を示す」という
同じ形をしているため、同居させて表示ルールの重複を避けている。
判断の是非そのものは扱わず、判断に必要な材料を並べるところまでが責務。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import QuerySet

from apps.dashboard.models import InterventionProposal
from apps.projects.models import ChangeRequest, Risk

#: 一覧の最大表示件数。
MAX_ROWS = 100

#: 高リスクとみなすスコア（確率×影響）。5 段階評価の 4×4 が下限。
HIGH_RISK_SCORE = 16


@dataclass(frozen=True)
class RiskRow:
    risk: Risk

    @property
    def tone(self) -> str:
        score = self.risk.score

        if score >= HIGH_RISK_SCORE:
            return "r"

        return "a" if score >= 9 else "g"

    @property
    def has_mitigation(self) -> bool:
        """対策が書かれているか。高スコアなのに空欄なら、それ自体が指摘対象。"""

        return bool(self.risk.mitigation.strip())


@dataclass(frozen=True)
class RiskReport:
    rows: tuple[RiskRow, ...]
    total: int
    high_count: int
    materialized_count: int
    without_mitigation: int

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return Risk.Status.choices

    @property
    def max_score(self) -> int:
        return max((row.risk.score for row in self.rows), default=0)


@dataclass(frozen=True)
class ChangeRow:
    change: ChangeRequest

    @property
    def tone(self) -> str:
        """スケジュール影響の大きさで色を決める。5 営業日超を要注意とする。"""

        impact = self.change.schedule_impact_days or 0

        if impact > 5:
            return "r"

        return "a" if impact > 0 else "g"

    @property
    def is_pending(self) -> bool:
        return self.change.status in (
            ChangeRequest.Status.DRAFT,
            ChangeRequest.Status.UNDER_REVIEW,
            ChangeRequest.Status.PENDING_APPROVAL,
        )


@dataclass(frozen=True)
class ChangeReport:
    rows: tuple[ChangeRow, ...]
    total: int
    pending_count: int
    approved_count: int
    total_effort_days: Decimal
    total_schedule_days: int

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return ChangeRequest.Status.choices


@dataclass(frozen=True)
class InterventionRow:
    proposal: InterventionProposal

    @property
    def tone(self) -> str:
        return {
            InterventionProposal.Status.PROPOSED: "b",
            InterventionProposal.Status.ACCEPTED: "g",
            InterventionProposal.Status.MODIFIED: "a",
            InterventionProposal.Status.REJECTED: "n",
            InterventionProposal.Status.DONE: "g",
        }.get(self.proposal.status, "n")

    @property
    def confidence_percent(self) -> int | None:
        """信頼度は 0.0-1.0 で保持。ルールベース提案は null なので判定しない。"""

        if self.proposal.confidence is None:
            return None

        return round(100 * self.proposal.confidence)

    @property
    def evidence_items(self) -> list:
        """根拠は list でも dict でも来るため、画面で回せる形に揃える。"""

        evidence = self.proposal.evidence

        if isinstance(evidence, dict):
            return [f"{key}: {value}" for key, value in evidence.items()]

        return list(evidence or [])


@dataclass(frozen=True)
class InterventionReport:
    rows: tuple[InterventionRow, ...]
    total: int
    proposed_count: int
    adopted_count: int
    rejected_count: int

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return InterventionProposal.Status.choices

    @property
    def adoption_percent(self) -> int:
        decided = self.adopted_count + self.rejected_count

        return round(100 * self.adopted_count / decided) if decided else 0


def build_risk_report(risks: QuerySet[Risk]) -> RiskReport:
    """リスク一覧。スコア順の並びは selectors 側で確定している。"""

    rows = tuple(RiskRow(risk=risk) for risk in risks[:MAX_ROWS])
    open_rows = [row for row in rows if row.risk.status != Risk.Status.CLOSED]

    return RiskReport(
        rows=rows,
        total=risks.count(),
        high_count=sum(1 for row in open_rows if row.risk.score >= HIGH_RISK_SCORE),
        materialized_count=sum(
            1 for row in rows if row.risk.status == Risk.Status.MATERIALIZED
        ),
        without_mitigation=sum(1 for row in open_rows if not row.has_mitigation),
    )


def build_change_report(changes: QuerySet[ChangeRequest]) -> ChangeReport:
    """変更要求一覧。工数とスケジュール影響は合計も出す（総量が判断材料になる）。"""

    rows = tuple(ChangeRow(change=change) for change in changes[:MAX_ROWS])

    return ChangeReport(
        rows=rows,
        total=changes.count(),
        pending_count=sum(1 for row in rows if row.is_pending),
        approved_count=sum(
            1 for row in rows if row.change.status == ChangeRequest.Status.APPROVED
        ),
        total_effort_days=sum(
            (row.change.estimated_effort_days or Decimal(0) for row in rows), Decimal(0)
        ),
        total_schedule_days=sum(row.change.schedule_impact_days or 0 for row in rows),
    )


def build_intervention_report(
    proposals: QuerySet[InterventionProposal],
) -> InterventionReport:
    """AI 介入提案一覧。採用・不採用の実績は PoC の効果測定にそのまま使う。"""

    rows = tuple(InterventionRow(proposal=proposal) for proposal in proposals[:MAX_ROWS])
    adopted = (InterventionProposal.Status.ACCEPTED, InterventionProposal.Status.MODIFIED,
               InterventionProposal.Status.DONE)

    return InterventionReport(
        rows=rows,
        total=proposals.count(),
        proposed_count=sum(
            1 for row in rows if row.proposal.status == InterventionProposal.Status.PROPOSED
        ),
        adopted_count=sum(1 for row in rows if row.proposal.status in adopted),
        rejected_count=sum(
            1 for row in rows if row.proposal.status == InterventionProposal.Status.REJECTED
        ),
    )
