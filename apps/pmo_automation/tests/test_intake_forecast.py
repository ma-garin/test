"""services/intake_forecast.py（シナリオ予測の悪化検知）を検証する。"""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.forecast.models import Confidence, ForecastSnapshot, Horizon
from apps.pmo_automation.models import PmoWorkItem, WorkKind
from apps.pmo_automation.services import intake_forecast
from apps.projects.models import Project, WbsTask

NOW = timezone.now()
TODAY = timezone.localdate()


class IntakeForecastTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.task = WbsTask.objects.create(project=self.project, wbs_code="1.1", name="設計")

    def _snapshot(self, **kwargs) -> ForecastSnapshot:
        defaults = {
            "project": self.project,
            "target": self.task,
            "as_of": NOW,
            "horizon": Horizon.ONE_WEEK,
            "baseline_date": TODAY,
            "forecast_date": TODAY,
            "variance_business_days": 0,
            "confidence": Confidence.HIGH,
        }
        defaults.update(kwargs)

        return ForecastSnapshot.objects.create(**defaults)


class IntakeFromForecastRegressionTests(IntakeForecastTestBase):
    def test_前回より悪化した予測はWork_Itemを作る(self) -> None:
        previous = self._snapshot(
            as_of=NOW - timedelta(hours=1), variance_business_days=1, forecast_date=TODAY + timedelta(days=1)
        )
        current = self._snapshot(
            as_of=NOW,
            variance_business_days=4,
            forecast_date=TODAY + timedelta(days=4),
            previous=previous,
        )

        result = intake_forecast.intake_from_forecast_regression(current)

        self.assertTrue(result.created)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
        self.assertEqual(result.work_item.kind, WorkKind.FORECAST_REVIEW)
        self.assertEqual(result.work_item.tenant_id, self.tenant.id)

    def test_改善した予測はintake対象ではない(self) -> None:
        previous = self._snapshot(
            as_of=NOW - timedelta(hours=1), variance_business_days=4, forecast_date=TODAY + timedelta(days=4)
        )
        current = self._snapshot(
            as_of=NOW,
            variance_business_days=1,
            forecast_date=TODAY + timedelta(days=1),
            previous=previous,
        )

        with self.assertRaises(ValueError):
            intake_forecast.intake_from_forecast_regression(current)

        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_算定不能な予測はintake対象ではない(self) -> None:
        snapshot = self._snapshot(
            confidence=Confidence.UNKNOWN,
            forecast_date=None,
            variance_business_days=None,
            missing_inputs=["no_calendar"],
        )

        with self.assertRaises(ValueError):
            intake_forecast.intake_from_forecast_regression(snapshot)

    def test_前回が無ければintake対象ではない(self) -> None:
        snapshot = self._snapshot()

        with self.assertRaises(ValueError):
            intake_forecast.intake_from_forecast_regression(snapshot)

    def test_dry_runはDBを変更しない(self) -> None:
        previous = self._snapshot(
            as_of=NOW - timedelta(hours=1), variance_business_days=1, forecast_date=TODAY + timedelta(days=1)
        )
        current = self._snapshot(
            as_of=NOW,
            variance_business_days=4,
            forecast_date=TODAY + timedelta(days=4),
            previous=previous,
        )

        result = intake_forecast.intake_from_forecast_regression(current, dry_run=True)

        self.assertTrue(result.created)
        self.assertIsNone(result.work_item)
        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_同じsnapshotを再度intakeしてもWork_Itemは増えない(self) -> None:
        previous = self._snapshot(
            as_of=NOW - timedelta(hours=1), variance_business_days=1, forecast_date=TODAY + timedelta(days=1)
        )
        current = self._snapshot(
            as_of=NOW,
            variance_business_days=4,
            forecast_date=TODAY + timedelta(days=4),
            previous=previous,
        )

        first = intake_forecast.intake_from_forecast_regression(current)
        second = intake_forecast.intake_from_forecast_regression(current)

        self.assertFalse(second.created)
        self.assertEqual(second.work_item.pk, first.work_item.pk)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
