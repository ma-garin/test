"""KPI の達成判定と年度集計。

方向（高いほど良い／低いほど良い）と集計方法（合計・平均・最新値）を
取り違えると、達成しているのに未達と出る。両方を固定する。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.performance.constants import KpiAggregation, KpiDirection
from apps.performance.models import KpiDefinition, KpiResult, KpiTarget
from apps.performance.services import kpi as kpi_service
from apps.performance.tests import factories


class JudgeTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.up = KpiDefinition(tenant=self.tenant, code="up", name="受注", direction=KpiDirection.UP)
        self.down = KpiDefinition(
            tenant=self.tenant, code="down", name="解約率", direction=KpiDirection.DOWN
        )

    def test_higher_is_better(self) -> None:
        self.assertEqual(kpi_service.judge(self.up, Decimal("100"), Decimal("120")), "achieved")
        self.assertEqual(kpi_service.judge(self.up, Decimal("100"), Decimal("95")), "warning")
        self.assertEqual(kpi_service.judge(self.up, Decimal("100"), Decimal("50")), "behind")

    def test_lower_is_better(self) -> None:
        self.assertEqual(kpi_service.judge(self.down, Decimal("3"), Decimal("2")), "achieved")
        self.assertEqual(kpi_service.judge(self.down, Decimal("3"), Decimal("3.2")), "warning")
        self.assertEqual(kpi_service.judge(self.down, Decimal("3"), Decimal("9")), "behind")

    def test_zero_actual_on_lower_is_better_is_achieved(self) -> None:
        """実績0はゼロ除算だが、判定は「達成」でなければならない。"""

        self.assertEqual(kpi_service.judge(self.down, Decimal("3"), Decimal("0")), "achieved")

    def test_missing_values(self) -> None:
        self.assertEqual(kpi_service.judge(self.up, None, Decimal("10")), "no_target")
        self.assertEqual(kpi_service.judge(self.up, Decimal("10"), None), "no_result")


class AggregationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.values = [
            (date(2026, 4, 1), Decimal("10")),
            (date(2026, 5, 1), Decimal("20")),
            (date(2026, 6, 1), Decimal("30")),
        ]

    def _kpi(self, aggregation: str) -> KpiDefinition:
        return KpiDefinition(
            tenant=self.tenant, code="k", name="k", aggregation=aggregation
        )

    def test_sum(self) -> None:
        self.assertEqual(
            kpi_service.aggregate_results(self._kpi(KpiAggregation.SUM), self.values), 60
        )

    def test_average_is_used_for_rates(self) -> None:
        self.assertEqual(
            kpi_service.aggregate_results(self._kpi(KpiAggregation.AVERAGE), self.values),
            Decimal("20.00"),
        )

    def test_latest_uses_the_newest_month(self) -> None:
        self.assertEqual(
            kpi_service.aggregate_results(self._kpi(KpiAggregation.LATEST), self.values), 30
        )

    def test_no_values_returns_none(self) -> None:
        self.assertIsNone(kpi_service.aggregate_results(self._kpi(KpiAggregation.SUM), []))


class KpiStatusTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant)
        self.version = factories.make_version(self.tenant, self.year)
        self.kpi = KpiDefinition.objects.create(
            tenant=self.tenant, code="orders", name="受注件数", unit="件"
        )
        self.month = date(2026, 4, 1)

    def test_target_without_result_is_reported_as_unmeasured(self) -> None:
        KpiTarget.objects.create(
            kpi=self.kpi,
            plan_version=self.version,
            org_unit=self.units["sec"],
            target_value=Decimal("10"),
        )

        statuses = kpi_service.kpi_statuses(
            self.year, [self.units["sec"].pk], [self.month], self.version
        )

        self.assertEqual(statuses[0].status, "no_result")
        self.assertEqual(kpi_service.KpiSummary(statuses=statuses).unmeasured, 1)

    def test_status_uses_target_of_the_given_version(self) -> None:
        KpiTarget.objects.create(
            kpi=self.kpi,
            plan_version=self.version,
            org_unit=self.units["sec"],
            target_value=Decimal("10"),
        )
        KpiResult.objects.create(
            tenant=self.tenant,
            kpi=self.kpi,
            fiscal_year=self.year,
            org_unit=self.units["sec"],
            month=self.month,
            actual_value=Decimal("12"),
        )

        statuses = kpi_service.kpi_statuses(
            self.year, [self.units["sec"].pk], [self.month], self.version
        )

        self.assertEqual(statuses[0].status, "achieved")
        self.assertEqual(statuses[0].achievement_ratio, Decimal("120.00"))

    def test_member_rows_are_excluded_from_org_level_status(self) -> None:
        member = factories.make_member(self.tenant, self.units["sec"])
        KpiResult.objects.create(
            tenant=self.tenant,
            kpi=self.kpi,
            fiscal_year=self.year,
            org_unit=self.units["sec"],
            member=member,
            month=self.month,
            actual_value=Decimal("5"),
        )

        statuses = kpi_service.kpi_statuses(
            self.year, [self.units["sec"].pk], [self.month], self.version
        )

        self.assertEqual(statuses, [])
