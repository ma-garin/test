"""月・年の計算。前年同期比較は月をそのまま1年ずらすだけなので、
うるう年の境界で日付が壊れないことを固定する。
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.performance.services.calendar import shift_year


class ShiftYearTests(TestCase):
    def test_shifts_year_only(self) -> None:
        self.assertEqual(shift_year(date(2026, 4, 1), -1), date(2025, 4, 1))
        self.assertEqual(shift_year(date(2025, 4, 1), 1), date(2026, 4, 1))

    def test_leap_day_falls_back_to_month_end(self) -> None:
        """2/29 を平年へずらすと存在しないので、2/28 に丸める。"""

        self.assertEqual(shift_year(date(2024, 2, 29), 1), date(2025, 2, 28))

    def test_month_start_is_always_safe(self) -> None:
        """計数は月初日でしか持たないため、実務上はこの経路しか通らない。"""

        for month in range(1, 13):
            original = date(2026, month, 1)
            self.assertEqual(shift_year(original, -1).month, month)
