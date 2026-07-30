"""人の判断が要る一覧（リスク・変更要求・AI介入提案）の集計。

3 つとも「一覧を出し、状態別の件数を添え、色で優先順位を示す」という
同じ形をしているため、同居させて表示ルールの重複を避けている。
判断の是非そのものは扱わず、判断に必要な材料を並べるところまでが責務。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, F, Max, Q, QuerySet, Sum
from django.db.models.functions import Trim

from apps.dashboard.models import InterventionProposal
from apps.projects.models import ChangeRequest, Risk

#: 高リスクとみなすスコア（確率×影響）。5 段階評価の 4×4 が下限。
HIGH_RISK_SCORE = 16

#: 未決とみなす変更要求の状態。行の色と集計で基準がずれないよう 1 箇所に置く。
PENDING_CHANGE_STATUSES = (
    ChangeRequest.Status.DRAFT,
    ChangeRequest.Status.UNDER_REVIEW,
    ChangeRequest.Status.PENDING_APPROVAL,
)

#: 採用として数える介入提案の状態。修正のうえ実施も「提案が活きた」に含める。
ADOPTED_INTERVENTION_STATUSES = (
    InterventionProposal.Status.ACCEPTED,
    InterventionProposal.Status.MODIFIED,
    InterventionProposal.Status.DONE,
)


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
    max_score: int

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return Risk.Status.choices


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
        return self.change.status in PENDING_CHANGE_STATUSES


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


def build_risk_report(
    risks: QuerySet[Risk],
    display_risks: Iterable[Risk] | None = None,
) -> RiskReport:
    """リスク一覧。スコア順の並びは selectors 側で確定している。

    `risks` は集計用の全件、`display_risks` は表示する 1 ページ分。
    「高リスク 5 件」がページごとに増減しては指標にならないため、件数は
    全件から数える。スコアはモデルのプロパティなので、同じ式を注釈して
    DB 側の条件に使う。
    """

    visible = risks if display_risks is None else display_risks
    rows = tuple(RiskRow(risk=risk) for risk in visible)
    open_only = ~Q(status=Risk.Status.CLOSED)
    summary = risks.annotate(
        score_value=F("probability") * F("impact"),
        mitigation_text=Trim("mitigation"),
    ).aggregate(
        total=Count("pk"),
        high_count=Count("pk", filter=Q(score_value__gte=HIGH_RISK_SCORE) & open_only),
        materialized_count=Count("pk", filter=Q(status=Risk.Status.MATERIALIZED)),
        without_mitigation=Count("pk", filter=Q(mitigation_text="") & open_only),
        max_score=Max("score_value"),
    )

    return RiskReport(
        rows=rows,
        total=summary["total"],
        high_count=summary["high_count"],
        materialized_count=summary["materialized_count"],
        without_mitigation=summary["without_mitigation"],
        max_score=summary["max_score"] or 0,
    )


def build_change_report(
    changes: QuerySet[ChangeRequest],
    display_changes: Iterable[ChangeRequest] | None = None,
) -> ChangeReport:
    """変更要求一覧。工数とスケジュール影響は合計も出す（総量が判断材料になる）。

    合計が「いま見えているページの総量」では判断材料にならないため、
    表示は 1 ページ分（`display_changes`）でも集計は全件（`changes`）から取る。
    """

    visible = changes if display_changes is None else display_changes
    rows = tuple(ChangeRow(change=change) for change in visible)
    summary = changes.aggregate(
        total=Count("pk"),
        pending_count=Count("pk", filter=Q(status__in=PENDING_CHANGE_STATUSES)),
        approved_count=Count("pk", filter=Q(status=ChangeRequest.Status.APPROVED)),
        effort_days=Sum("estimated_effort_days"),
        schedule_days=Sum("schedule_impact_days"),
    )

    return ChangeReport(
        rows=rows,
        total=summary["total"],
        pending_count=summary["pending_count"],
        approved_count=summary["approved_count"],
        total_effort_days=summary["effort_days"] or Decimal(0),
        total_schedule_days=summary["schedule_days"] or 0,
    )


def build_intervention_report(
    proposals: QuerySet[InterventionProposal],
    display_proposals: Iterable[InterventionProposal] | None = None,
) -> InterventionReport:
    """AI 介入提案一覧。採用・不採用の実績は PoC の効果測定にそのまま使う。

    採用率はそのまま報告に載る数字なので、表示ページに引きずられてはいけない。
    集計は全件（`proposals`）から取る。
    """

    visible = proposals if display_proposals is None else display_proposals
    rows = tuple(InterventionRow(proposal=proposal) for proposal in visible)
    summary = proposals.aggregate(
        total=Count("pk"),
        proposed_count=Count("pk", filter=Q(status=InterventionProposal.Status.PROPOSED)),
        adopted_count=Count("pk", filter=Q(status__in=ADOPTED_INTERVENTION_STATUSES)),
        rejected_count=Count("pk", filter=Q(status=InterventionProposal.Status.REJECTED)),
    )

    return InterventionReport(rows=rows, **summary)
