"""LDF-04: ライブ着地予測の表示データ。

画面が計算方法を知らなくてよいように、危険な順に並べたところまでをここで作る。
「30秒以内に、どこで危険になるかと確信度を区別できる」ことが受入条件なので、
遅延・算定不能・低確信度を混ぜずに数える。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from apps.forecast.models.snapshots import Confidence, ForecastSnapshot, Horizon
from apps.forecast.services.engine import TargetForecast, compute_project_forecast

#: ダッシュボードの先頭に出す件数。読み切れる数に抑える。
TOP_RISK_LIMIT = 5

#: 確信度の表示順（危ない順）。
CONFIDENCE_TONE = {
    Confidence.HIGH: "g",
    Confidence.MEDIUM: "a",
    Confidence.LOW: "r",
    Confidence.UNKNOWN: "n",
}


@dataclass(frozen=True)
class ForecastRow:
    """1 マイルストーン・1 地平の表示行。"""

    project: object
    forecast: TargetForecast
    previous: ForecastSnapshot | None

    @property
    def target_name(self) -> str:
        return str(self.forecast.target)

    @property
    def tone(self) -> str:
        return CONFIDENCE_TONE.get(self.forecast.confidence, "n")

    @property
    def variance_label(self) -> str:
        """日数の表示。算定不能を 0 日と見せない。"""

        variance = self.forecast.variance_business_days
        if variance is None:
            return "算定不能"
        if variance > 0:
            return f"{variance} 営業日 遅延"
        if variance < 0:
            return f"{abs(variance)} 営業日 前倒し"
        return "予定どおり"

    @property
    def change_from_previous(self) -> int | None:
        if self.previous is None or self.previous.variance_business_days is None:
            return None
        if self.forecast.variance_business_days is None:
            return None
        return self.forecast.variance_business_days - self.previous.variance_business_days

    @property
    def risk_rank(self) -> tuple:
        """危険な順。遅延が大きいほど、算定不能、確信度が低いほど前に出す。"""

        variance = self.forecast.variance_business_days
        return (
            0 if (variance or 0) > 0 else (1 if variance is None else 2),
            -(variance or 0),
            0 if self.forecast.confidence == Confidence.LOW else 1,
            self.target_name,
        )


@dataclass(frozen=True)
class ForecastBoard:
    """ライブ着地予測画面が必要とするものすべて。"""

    rows: tuple[ForecastRow, ...] = ()
    two_day: tuple[ForecastRow, ...] = ()
    one_week: tuple[ForecastRow, ...] = ()
    latest_snapshot_at: object | None = None
    projects_without_calendar: tuple[str, ...] = ()

    @property
    def top_risks(self) -> tuple[ForecastRow, ...]:
        return self.rows[:TOP_RISK_LIMIT]

    @property
    def delayed_count(self) -> int:
        return sum(
            1
            for row in self.rows
            if (row.forecast.variance_business_days or 0) > 0
        )

    @property
    def undeterminable_count(self) -> int:
        return sum(1 for row in self.rows if row.forecast.is_undeterminable)

    @property
    def low_confidence_count(self) -> int:
        return sum(1 for row in self.rows if row.forecast.confidence == Confidence.LOW)

    @property
    def horizon_risk_count(self) -> int:
        """2日後・1週間後の時点で遅れる、または算定できない見込みの件数。"""

        return sum(
            1
            for row in (*self.two_day, *self.one_week)
            if row.forecast.is_undeterminable or (row.forecast.variance_business_days or 0) > 0
        )

    @property
    def is_empty(self) -> bool:
        return not self.rows and not self.two_day and not self.one_week


def build_forecast_board(projects, as_of: date) -> ForecastBoard:
    """参照できる案件の着地予測をまとめる。"""

    milestone_rows: list[ForecastRow] = []
    two_day: list[ForecastRow] = []
    one_week: list[ForecastRow] = []
    without_calendar: list[str] = []

    for project in projects:
        computed = compute_project_forecast(project, as_of)
        if computed.calendar_missing:
            without_calendar.append(project.code)

        for target in computed.targets:
            row = ForecastRow(
                project=project,
                forecast=target,
                previous=ForecastSnapshot.objects.latest_for(target.target, target.horizon),
            )
            if target.horizon == Horizon.MILESTONE:
                milestone_rows.append(row)
            elif target.horizon == Horizon.TWO_DAYS:
                two_day.append(row)
            else:
                one_week.append(row)

    milestone_rows.sort(key=lambda row: row.risk_rank)
    latest = (
        ForecastSnapshot.objects.filter(project__in=projects).order_by("-as_of").first()
    )

    return ForecastBoard(
        rows=tuple(milestone_rows),
        two_day=tuple(sorted(two_day, key=lambda row: row.risk_rank)),
        one_week=tuple(sorted(one_week, key=lambda row: row.risk_rank)),
        latest_snapshot_at=latest.as_of if latest else None,
        projects_without_calendar=tuple(without_calendar),
    )
