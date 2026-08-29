"""組織ツリーの積み上げと率の算出。

ここが崩れると、部の数字と課の数字が合わない状態で意思決定されるため、
二重計上の不在と、売上0での率の扱いを固定する。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.performance.services import aggregation
from apps.performance.tests import factories


class RollupTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant)
        self.version = factories.make_version(self.tenant, self.year)
        self.month = date(2026, 4, 1)

    def _report(self, members=None):
        return aggregation.build_report(
            self.year, list(self.units.values()), [self.month], members or []
        )

    def test_parent_sums_own_and_descendants(self) -> None:
        factories.add_actual(self.year, self.units["div"], self.month, 100)
        factories.add_actual(self.year, self.units["sec"], self.month, 200)
        factories.add_actual(self.year, self.units["prj"], self.month, 300)

        report = self._report()

        self.assertEqual(report.for_unit(self.units["prj"]).total_actual.revenue, 300)
        self.assertEqual(report.for_unit(self.units["sec"]).total_actual.revenue, 500)
        self.assertEqual(report.for_unit(self.units["div"]).total_actual.revenue, 600)

    def test_member_figures_are_not_added_to_org_total(self) -> None:
        """個人値は組織値の内訳。足すと配分を入れた組織だけ倍になる。"""

        member = factories.make_member(self.tenant, self.units["sec"])
        factories.add_actual(self.year, self.units["sec"], self.month, 200)
        factories.add_actual(self.year, self.units["sec"], self.month, 120, member=member)

        summary = self._report([member]).for_unit(self.units["sec"])

        self.assertEqual(summary.total_actual.revenue, 200)
        self.assertEqual(summary.member_actual.revenue, 120)
        self.assertEqual(summary.member_gap.revenue, -80)

    def test_totals_do_not_double_count_nested_roots(self) -> None:
        factories.add_actual(self.year, self.units["sec"], self.month, 200)

        report = self._report()
        total = report.totals([self.units["div"], self.units["sec"]])

        self.assertEqual(total.actual.revenue, 200)

    def test_rate_is_none_when_revenue_is_zero(self) -> None:
        amounts = aggregation.Amounts(
            revenue=Decimal("0"), gross_profit=Decimal("0"), operating_profit=Decimal("5")
        )

        self.assertIsNone(amounts.profit_rate)
        self.assertIsNone(amounts.gross_margin_rate)

    def test_achievement_and_variance(self) -> None:
        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 900)

        summary = self._report().for_unit(self.units["sec"])

        self.assertEqual(summary.comparison.revenue_achievement, Decimal("90.00"))
        self.assertEqual(summary.comparison.variance.revenue, -100)
        self.assertEqual(summary.comparison.tone, "a")

    def test_monthly_rows_accumulate(self) -> None:
        may = date(2026, 5, 1)
        factories.add_actual(self.year, self.units["sec"], self.month, 100)
        factories.add_actual(self.year, self.units["sec"], may, 150)

        report = aggregation.build_report(self.year, list(self.units.values()), [self.month, may])
        rows = report.monthly_rows(self.units["sec"])

        self.assertEqual(rows[0].cumulative_actual.revenue, 100)
        self.assertEqual(rows[1].cumulative_actual.revenue, 250)


class MemberAllocationTests(TestCase):
    """個人配分の警告は「入れすぎ」だけに出す。

    一部のメンバーだけ個人別に管理する運用が普通なので、不足で毎回警告すると
    本当に見るべき行が埋もれる。
    """

    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant)
        self.member = factories.make_member(self.tenant, self.units["sec"])
        self.month = date(2026, 4, 1)

    def _summary(self):
        report = aggregation.build_report(
            self.year, list(self.units.values()), [self.month], [self.member]
        )

        return report.for_unit(self.units["sec"])

    def test_partial_allocation_is_not_flagged(self) -> None:
        factories.add_actual(self.year, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 400, member=self.member)

        self.assertFalse(self._summary().member_over_allocated)

    def test_over_allocation_is_flagged(self) -> None:
        factories.add_actual(self.year, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 1200, member=self.member)

        summary = self._summary()

        self.assertTrue(summary.member_over_allocated)
        self.assertEqual(summary.member_gap.revenue, 200)
