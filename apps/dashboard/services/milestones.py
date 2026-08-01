"""マイルストーンの予実（計画・見込・実績）集計。

マイルストーンは「いつまでに何が終わるか」の約束であり、遅れは
タスク単位の進捗率より先に経営へ効く。計画日だけを持っていても
「間に合うのか」に答えられないため、計画・見込・実績の 3 つを並べ、
遅延日数を数値で出す。品質ゲートは、遅れるとその先が全部止まるため強調する。

AI は使わず、登録済みの日付だけで判定する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import QuerySet
from django.utils import timezone

from apps.projects.models import Milestone

#: 進捗予測画面に出すマイルストーンの上限。多すぎると表として読めなくなる。
MAX_MILESTONES = 30


@dataclass(frozen=True)
class MilestoneRow:
    """1 マイルストーンの予実。"""

    milestone: Milestone
    today: date

    @property
    def is_gate(self) -> bool:
        return self.milestone.is_gate

    @property
    def is_done(self) -> bool:
        return self.milestone.actual_date is not None

    @property
    def actual_delay_days(self) -> int | None:
        """実績の遅延日数。実績日が無ければ None（まだ確定していない）。"""

        if self.milestone.actual_date is None:
            return None

        return (self.milestone.actual_date - self.milestone.planned_date).days

    @property
    def forecast_delay_days(self) -> int | None:
        """見込みの遅延日数。見込日が無ければ None。"""

        if self.milestone.forecast_date is None:
            return None

        return (self.milestone.forecast_date - self.milestone.planned_date).days

    @property
    def delay_days(self) -> int:
        """いま採用すべき遅延日数。

        実績が出ていればそれが確定値。出ていなければ見込日で測る。
        見込日も無い場合、計画日を過ぎていれば本日基準で測る
        （見込みを入れていないことを「遅れていない」と読ませない）。
        """

        if self.actual_delay_days is not None:
            return self.actual_delay_days

        if self.forecast_delay_days is not None:
            return self.forecast_delay_days

        return max((self.today - self.milestone.planned_date).days, 0)

    @property
    def is_slipping(self) -> bool:
        """見込みが計画を超えているか。未達のうちに手を打つべき対象。"""

        return not self.is_done and self.delay_days > 0

    @property
    def basis_label(self) -> str:
        """遅延日数を何で測ったか。根拠を明示しないと数字を信用できない。"""

        if self.actual_delay_days is not None:
            return "実績日基準"

        if self.forecast_delay_days is not None:
            return "見込日基準"

        return "本日基準（見込日未入力）"

    @property
    def tone(self) -> str:
        """バッジの色。品質ゲートの遅れは 1 日でも赤にする。"""

        if self.delay_days <= 0:
            return "g" if self.is_done else "n"

        if self.is_gate or self.delay_days >= 7:
            return "r"

        return "a"

    @property
    def status_label(self) -> str:
        if self.is_done:
            return "達成" if self.delay_days <= 0 else f"遅延達成 +{self.delay_days}日"

        if self.delay_days > 0:
            return f"遅延見込み +{self.delay_days}日"

        return "計画どおり"


@dataclass(frozen=True)
class MilestoneReport:
    rows: tuple[MilestoneRow, ...]

    @property
    def has_rows(self) -> bool:
        return bool(self.rows)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def slipping_rows(self) -> tuple[MilestoneRow, ...]:
        """見込みが計画日を超えているマイルストーン。警告表示の対象。"""

        return tuple(row for row in self.rows if row.is_slipping)

    @property
    def slipping_gates(self) -> tuple[MilestoneRow, ...]:
        return tuple(row for row in self.slipping_rows if row.is_gate)

    @property
    def worst(self) -> MilestoneRow | None:
        """最も遅れている未達のマイルストーン。"""

        slipping = self.slipping_rows

        return max(slipping, key=lambda row: row.delay_days) if slipping else None

    @property
    def max_delay_days(self) -> int:
        worst = self.worst

        return worst.delay_days if worst else 0


def build_milestone_report(
    milestones: QuerySet[Milestone], *, today: date | None = None
) -> MilestoneReport:
    """マイルストーンの予実表を作る。

    引数はテナント分離済みの QuerySet を前提にする
    （呼び出し側が `scoped_projects_for()` 由来の案件で絞る）。
    """

    reference = today or timezone.localdate()
    rows = tuple(
        MilestoneRow(milestone=milestone, today=reference)
        for milestone in milestones[:MAX_MILESTONES]
    )

    return MilestoneReport(rows=rows)
