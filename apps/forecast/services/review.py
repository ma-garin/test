"""AH-07: 予測・関連候補への人のレビューと、実績フィードバック。

AI の候補は候補のままにする。人が採用・修正・却下したことを記録し、
予測と実績の差を残して次の評価に使う。

`docs/改善に.md`:「採用率だけを最適化しない」。そのため、採否と結果を別々に数える。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import Count, Q

from apps.forecast.models.snapshots import (
    Confidence,
    ForecastReview,
    ForecastSnapshot,
    Horizon,
)
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState
from apps.projects.models import Milestone


class ReviewError(ValueError):
    """人の判断として記録できない操作。"""


@dataclass(frozen=True)
class AccuracyRow:
    """1 マイルストーンの、予測と実績の差。"""

    milestone: Milestone
    predicted_date: date | None
    actual_date: date | None
    confidence_at_prediction: str

    @property
    def error_days(self) -> int | None:
        """予測日と実績日の差（暦日）。営業日換算は案件カレンダーに依存するため別途。"""

        if self.predicted_date is None or self.actual_date is None:
            return None
        return (self.actual_date - self.predicted_date).days

    @property
    def is_measurable(self) -> bool:
        return self.error_days is not None


@dataclass(frozen=True)
class AccuracyReport:
    """予測の校正に使う指標。採用率だけでは品質を測らない。"""

    rows: tuple[AccuracyRow, ...] = ()
    adopted: int = 0
    corrected: int = 0
    rejected: int = 0
    unreviewed: int = 0

    @property
    def measurable(self) -> tuple[AccuracyRow, ...]:
        return tuple(row for row in self.rows if row.is_measurable)

    @property
    def mean_absolute_error(self) -> float | None:
        """平均絶対誤差。測れる実績が無ければ None（0 と混同しない）。"""

        measurable = self.measurable
        if not measurable:
            return None
        return round(sum(abs(row.error_days) for row in measurable) / len(measurable), 1)

    @property
    def reviewed_total(self) -> int:
        return self.adopted + self.corrected + self.rejected

    @property
    def review_rate(self) -> float | None:
        total = self.reviewed_total + self.unreviewed
        return round(self.reviewed_total / total, 2) if total else None


def record_review(
    snapshot: ForecastSnapshot,
    user,
    *,
    decision: str,
    reason: str = "",
    corrected_date: date | None = None,
) -> ForecastReview:
    """予測への人の判断を記録する。

    `算定不能` の予測を「採用」できないようにする。根拠が無いものを人が承認した形に
    見せると、後から「誰が認めたのか」の説明が崩れる。
    """

    if snapshot.is_undeterminable and decision == ForecastReview.Decision.ADOPT:
        raise ReviewError(
            "算定不能の予測は採用できません。不足入力を解消するか、却下してください。"
        )

    return ForecastReview.objects.create(
        snapshot=snapshot,
        reviewer=user,
        decision=decision,
        reason=reason,
        corrected_date=corrected_date,
    )


def review_link(link: WorkLink, user, *, confirm: bool, reason: str = "") -> WorkLink:
    """関連候補の確定・否定。AI が確定させたように見せないため確認者を必ず残す。"""

    if link.state in (LinkState.CONFIRMED, LinkState.REJECTED) and not confirm:
        # 否定済みの候補を再提案しない、という規則の裏返し。二重否定は無害だが記録は残す。
        pass
    return link.confirm(user, reason) if confirm else link.reject(user, reason)


def accuracy_report(project) -> AccuracyReport:
    """予測と実績の比較。実績日が入ったマイルストーンだけを測る。"""

    milestones = list(Milestone.objects.filter(project=project))
    rows: list[AccuracyRow] = []

    for milestone in milestones:
        snapshot = ForecastSnapshot.objects.latest_for(milestone, Horizon.MILESTONE)
        if snapshot is None:
            continue
        rows.append(
            AccuracyRow(
                milestone=milestone,
                predicted_date=snapshot.forecast_date,
                actual_date=milestone.actual_date,
                confidence_at_prediction=snapshot.confidence,
            )
        )

    counts = ForecastReview.objects.filter(snapshot__project=project).aggregate(
        adopted=Count("pk", filter=Q(decision=ForecastReview.Decision.ADOPT)),
        corrected=Count("pk", filter=Q(decision=ForecastReview.Decision.CORRECT)),
        rejected=Count("pk", filter=Q(decision=ForecastReview.Decision.REJECT)),
    )
    unreviewed = (
        ForecastSnapshot.objects.filter(project=project, reviews__isnull=True)
        .exclude(confidence=Confidence.UNKNOWN)
        .count()
    )

    return AccuracyReport(rows=tuple(rows), unreviewed=unreviewed, **counts)
