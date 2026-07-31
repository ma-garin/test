"""マイルストーンの予実差分析（要件 #4）。

WBS の予実は `progress.py` が見ている。だがタスクが計画どおりでも、
節目（設計完了、結合試験完了、本番リリース）がずれていれば案件は遅れている。
逆にタスクが数件遅れていても、節目に間に合うなら騒ぐ必要はない。

**ずれの向きを 1 つの数（`slip_days`）に統一する。** 正なら後ろ倒し、負なら前倒し。
比較対象は、実績日があれば実績日、無ければ見込日、どちらも無ければ今日とする
（今日を使うのは、期日を過ぎても実績が入っていないケースを「遅れ 0」と
言わないため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import QuerySet
from django.utils import timezone

from apps.projects.models import Milestone, Project

#: 何日ずれたら「要対応」と見なすか。営業日ではなく暦日で数える。
CRITICAL_SLIP_DAYS = 5


@dataclass(frozen=True)
class MilestoneRow:
    """マイルストーン 1 件の予実。"""

    milestone: Milestone
    today: date

    @property
    def project(self) -> Project:
        return self.milestone.project

    @property
    def is_done(self) -> bool:
        return self.milestone.actual_date is not None

    @property
    def reference_date(self) -> date:
        """実績日 → 見込日 → 今日 の順に、比較対象を決める。"""

        if self.milestone.actual_date:
            return self.milestone.actual_date

        if self.milestone.forecast_date:
            return self.milestone.forecast_date

        return max(self.today, self.milestone.planned_date)

    @property
    def slip_days(self) -> int:
        """計画日からのずれ。正が後ろ倒し。"""

        return (self.reference_date - self.milestone.planned_date).days

    @property
    def is_late(self) -> bool:
        return self.slip_days > 0

    @property
    def basis(self) -> str:
        """どの日付と比べた数字なのかを画面へ出す。根拠を隠さない。"""

        if self.milestone.actual_date:
            return "実績日と比較"

        if self.milestone.forecast_date:
            return "見込日と比較"

        if self.today > self.milestone.planned_date:
            return "実績・見込とも未入力のため本日と比較"

        return "計画日は未到来"

    @property
    def state_label(self) -> str:
        if self.is_done:
            return "達成（遅延）" if self.is_late else "達成"

        if not self.is_late:
            return "計画どおり"

        return "遅延" if self.slip_days >= CRITICAL_SLIP_DAYS else "遅れ"

    @property
    def tone(self) -> str:
        if self.is_done:
            return "a" if self.is_late else "g"

        if not self.is_late:
            return "n"

        return "r" if self.slip_days >= CRITICAL_SLIP_DAYS else "a"

    @property
    def slip_label(self) -> str:
        if self.slip_days == 0:
            return "ずれなし"

        if self.slip_days > 0:
            return f"{self.slip_days}日 後ろ倒し"

        return f"{abs(self.slip_days)}日 前倒し"


@dataclass(frozen=True)
class MilestoneReport:
    rows: tuple[MilestoneRow, ...]

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def late(self) -> tuple[MilestoneRow, ...]:
        return tuple(row for row in self.rows if row.is_late)

    @property
    def late_count(self) -> int:
        return len(self.late)

    @property
    def gate_late_count(self) -> int:
        """品質ゲートの遅れは別に数える。ゲートは飛ばせない。"""

        return sum(1 for row in self.late if row.milestone.is_gate)

    @property
    def worst(self) -> MilestoneRow | None:
        return max(self.rows, key=lambda row: row.slip_days) if self.rows else None

    @property
    def max_slip_days(self) -> int:
        worst = self.worst

        return worst.slip_days if worst is not None else 0

    @property
    def upcoming(self) -> tuple[MilestoneRow, ...]:
        """未達のものだけ。達成済みは予定として出さない。"""

        return tuple(row for row in self.rows if not row.is_done)


def build_milestone_report(
    projects: QuerySet[Project], today: date | None = None
) -> MilestoneReport:
    """参照できる案件のマイルストーンを、計画日順に予実で並べる。"""

    today = today or timezone.localdate()
    milestones = (
        Milestone.objects.filter(project__in=projects)
        .select_related("project")
        .order_by("planned_date", "name")
    )

    return MilestoneReport(
        rows=tuple(MilestoneRow(milestone=milestone, today=today) for milestone in milestones)
    )
