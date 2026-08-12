"""GE-02: 営業日の計算。

暦日を営業日として扱うと、金曜に 1 日足して土曜を「着地日」と表示してしまう。
勤務日は案件ごとのカレンダーで決め、カレンダーが無い案件では日数を推測しない。

`docs/改善に.md`: 「勤務日・制約が欠けた場合、暦日を営業日として扱わず、
日数予測を算定不能とする」。この方針をここで一元化する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from apps.graph.models.schedule import CalendarDay, WorkingCalendar

#: 1 回の計算でたどる暦日の上限。カレンダー設定の誤りで無限ループしないようにする。
MAX_SCAN_DAYS = 3650


@dataclass(frozen=True)
class BusinessCalendar:
    """勤務日の判定に必要なものだけを持つ値オブジェクト。

    DB を都度引かないよう、案件ごとに 1 度だけ読み込んで使い回す。
    """

    working_weekdays: frozenset[int]
    holidays: frozenset[date]
    extra_workdays: frozenset[date]
    freeze_days: frozenset[date]

    @classmethod
    def for_project(cls, project) -> BusinessCalendar | None:
        """案件のカレンダー。無ければ None を返す（暦日で代用しない）。"""

        calendar = (
            WorkingCalendar.objects.filter(project=project).prefetch_related("days").first()
        )
        if calendar is None:
            return None

        by_kind: dict[str, set[date]] = {kind.value: set() for kind in CalendarDay.Kind}
        for day in calendar.days.all():
            by_kind[day.kind].add(day.date)

        return cls(
            working_weekdays=calendar.weekday_numbers,
            holidays=frozenset(by_kind[CalendarDay.Kind.HOLIDAY]),
            extra_workdays=frozenset(by_kind[CalendarDay.Kind.WORKDAY]),
            freeze_days=frozenset(by_kind[CalendarDay.Kind.FREEZE]),
        )

    def is_working_day(self, day: date) -> bool:
        """臨時稼働日は休日指定より優先する（振替出勤を表現するため）。"""

        if day in self.extra_workdays:
            return True
        if day in self.holidays:
            return False
        return day.weekday() in self.working_weekdays

    def is_frozen(self, day: date) -> bool:
        """リリース凍結日。作業はできてもリリースは置けない。"""

        return day in self.freeze_days

    def add_business_days(self, start: date, days: int) -> date:
        """`start` から営業日を足した日付。0 なら直近の営業日へ寄せる。"""

        current = _next_working_day(self, start) if not self.is_working_day(start) else start
        step = 1 if days >= 0 else -1
        remaining = abs(days)
        scanned = 0
        while remaining > 0:
            current += timedelta(days=step)
            scanned += 1
            if scanned > MAX_SCAN_DAYS:
                raise ValueError("営業日が見つかりません。勤務カレンダーの設定を確認してください。")
            if self.is_working_day(current):
                remaining -= 1
        return current

    def business_days_between(self, start: date, end: date) -> int:
        """`start` から `end` までの営業日数。`end` が前なら負値を返す。

        遅延／前倒しの日数はこの値で出す。暦日の差を使わない。
        """

        if start == end:
            return 0

        step = 1 if end > start else -1
        current, count, scanned = start, 0, 0
        while current != end:
            current += timedelta(days=step)
            scanned += 1
            if scanned > MAX_SCAN_DAYS:
                raise ValueError("営業日の計算範囲を超えました。日付の指定を確認してください。")
            if self.is_working_day(current):
                count += step
        return count


def _next_working_day(calendar: BusinessCalendar, start: date) -> date:
    current, scanned = start, 0
    while not calendar.is_working_day(current):
        current += timedelta(days=1)
        scanned += 1
        if scanned > MAX_SCAN_DAYS:
            raise ValueError("勤務日が 1 日もありません。カレンダー設定を確認してください。")
    return current
