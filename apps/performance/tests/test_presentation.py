"""計数サマリ表の前年同期比較（`summary_rows` / `SummaryRow`）。

率の行（粗利率・利益率）は前年比を出さない。率どうしの比は意味を持たず、
見るべきは差（ポイント）だから。金額の行はその逆で、差ではなく比（%）を出す。
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from apps.performance.services.aggregation import Amounts, Comparison
from apps.performance.services.presentation import summary_rows


class SummaryRowsPriorYearTests(TestCase):
    def setUp(self) -> None:
        self.comparison = Comparison(
            plan=Amounts(
                revenue=Decimal("1000"), gross_profit=Decimal("300"), operating_profit=Decimal("100")
            ),
            actual=Amounts(
                revenue=Decimal("1100"), gross_profit=Decimal("330"), operating_profit=Decimal("110")
            ),
        )

    def test_without_prior_year_every_yoy_field_is_none(self) -> None:
        """前年度が無いとき、前年比を「0%」ではなく未確認として扱う。"""

        rows = {row.label: row for row in summary_rows(self.comparison)}

        for row in rows.values():
            self.assertIsNone(row.prior_actual)
            self.assertIsNone(row.yoy_diff)
            self.assertIsNone(row.yoy_rate)
            self.assertEqual(row.yoy_tone, "n")

    def test_amount_row_reports_yoy_rate_not_diff_alone(self) -> None:
        prior = Amounts(
            revenue=Decimal("1000"), gross_profit=Decimal("250"), operating_profit=Decimal("80")
        )
        rows = {row.label: row for row in summary_rows(self.comparison, prior)}

        revenue = rows["売上"]
        self.assertEqual(revenue.prior_actual, Decimal("1000"))
        self.assertEqual(revenue.yoy_diff, Decimal("100"))
        self.assertEqual(revenue.yoy_rate, Decimal("110.00"))
        self.assertEqual(revenue.yoy_tone, "g")

    def test_rate_row_reports_point_difference_not_a_ratio(self) -> None:
        """率どうしの比（実績÷前年実績）は意味を持たないので、比は出さない。"""

        prior = Amounts(
            revenue=Decimal("1000"), gross_profit=Decimal("250"), operating_profit=Decimal("80")
        )
        rows = {row.label: row for row in summary_rows(self.comparison, prior)}

        profit_rate_row = rows["利益率"]
        self.assertIsNone(profit_rate_row.yoy_rate)
        self.assertIsNotNone(profit_rate_row.yoy_diff)
        # 実績利益率 10.00% − 前年利益率 8.00% = +2.00pt
        self.assertEqual(profit_rate_row.yoy_diff, Decimal("2.00"))
        self.assertEqual(profit_rate_row.yoy_tone, "g")

    def test_decline_is_reported_with_negative_tone(self) -> None:
        prior = Amounts(
            revenue=Decimal("2000"), gross_profit=Decimal("500"), operating_profit=Decimal("200")
        )
        rows = {row.label: row for row in summary_rows(self.comparison, prior)}

        revenue = rows["売上"]
        self.assertLess(revenue.yoy_rate, Decimal("100"))
        self.assertEqual(revenue.yoy_tone, "r")


class DisplayUnitTests(TestCase):
    """金額の表示単位（円・千円・百万円）。

    1億を超える数字が並ぶ画面では、円のままだと桁が読めない。
    率には効かせない（率は単位を持たない）。
    """

    def setUp(self) -> None:
        self.comparison = Comparison(
            plan=Amounts(
                revenue=Decimal("192000000"),
                gross_profit=Decimal("56400000"),
                operating_profit=Decimal("25200000"),
            ),
            actual=Amounts(
                revenue=Decimal("180360000"),
                gross_profit=Decimal("52884000"),
                operating_profit=Decimal("23544000"),
            ),
        )

    def _rows(self, unit):
        from apps.performance.services.presentation import summary_rows

        return {row.label: row for row in summary_rows(self.comparison, None, unit)}

    def test_yen_keeps_the_raw_amount(self) -> None:
        self.assertEqual(self._rows("yen")["売上"].actual_display, Decimal("180360000"))

    def test_thousand_divides_by_1000(self) -> None:
        self.assertEqual(self._rows("thousand")["売上"].actual_display, Decimal("180360"))

    def test_million_divides_by_1000000(self) -> None:
        self.assertEqual(self._rows("million")["売上"].actual_display, Decimal("180.36"))

    def test_rate_rows_are_never_scaled(self) -> None:
        """率は単位を持たない。百万円を選んでも 13.1% は 13.1% のまま。"""

        rows = self._rows("million")
        self.assertEqual(rows["利益率"].actual_display, rows["利益率"].actual)

    def test_diff_is_scaled_too(self) -> None:
        """差異だけ円のままだと、実績と並べたとき桁が食い違う。"""

        row = self._rows("million")["売上"]
        self.assertEqual(row.diff_display, Decimal("-11.64"))

    def test_unknown_unit_falls_back_to_yen(self) -> None:
        from apps.performance.services.presentation import DEFAULT_UNIT, UNIT_KEYS

        self.assertEqual(DEFAULT_UNIT, "yen")
        self.assertNotIn("bogus", UNIT_KEYS)
