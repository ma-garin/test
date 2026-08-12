"""SEC-11: 大量イベント流入時の上限機構（部分実装）を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.dashboard.models import Alert
from apps.pmo_automation.models import PmoWorkItem, WorkItemState, WorkKind
from apps.pmo_automation.services import intake
from apps.pmo_automation.services.rate_limit import RateLimitExceeded, check_intake_rate_limit
from apps.projects.models import Project

NOW = timezone.now()


class CheckIntakeRateLimitTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _work_item(self, key: str) -> PmoWorkItem:
        return PmoWorkItem.objects.create(
            tenant=self.tenant,
            project=self.project,
            kind=WorkKind.DETECTION_TRIAGE,
            source_type="alert",
            source_key=key,
            dedupe_key=f"alert:{key}",
            state=WorkItemState.NEW,
        )

    def test_上限未満なら通過する(self) -> None:
        self._work_item("1")
        self._work_item("2")

        check_intake_rate_limit(self.tenant, now=NOW, max_count=5)

    def test_上限に達すると拒否される(self) -> None:
        self._work_item("1")
        self._work_item("2")

        with self.assertRaises(RateLimitExceeded):
            check_intake_rate_limit(self.tenant, now=NOW, max_count=2)

    def test_window外のWork_Itemはカウントされない(self) -> None:
        old_item = self._work_item("old")
        # created_atはauto_now_addのため直接更新する。
        PmoWorkItem.objects.filter(pk=old_item.pk).update(created_at=NOW - timedelta(hours=1))

        # window内には0件のため、max_count=1でも拒否されない。
        check_intake_rate_limit(self.tenant, now=NOW, window_seconds=60, max_count=1)

    def test_tenant単位で独立している(self) -> None:
        other_tenant = Tenant.objects.create(code="other", name="OTHER")
        self._work_item("1")
        self._work_item("2")

        # self.tenantは上限に達しているが、other_tenantには影響しない。
        with self.assertRaises(RateLimitExceeded):
            check_intake_rate_limit(self.tenant, now=NOW, max_count=2)
        check_intake_rate_limit(other_tenant, now=NOW, max_count=2)


class LargeVolumeDedupeTests(TestCase):
    """SEC-11: 大量の同一イベントでも dedupe により Work Item は1件にしかならず、
    正常な別案件の処理は妨げられない（1万件は重いのでテストでは50件に縮小）。
    """

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def test_同一Alertの大量intakeはWork_Itemを1件にしか作らない(self) -> None:
        alert = Alert.objects.create(
            project=self.project,
            category=Alert.Category.SCHEDULE,
            title="繰り返し検知",
            detected_at=NOW,
        )

        for _ in range(50):
            intake.intake_from_alert(alert)

        self.assertEqual(PmoWorkItem.objects.filter(tenant=self.tenant).count(), 1)

    def test_同一Alertの大量intake中でも別案件の正常な検知は独立して処理される(self) -> None:
        alert = Alert.objects.create(
            project=self.project,
            category=Alert.Category.SCHEDULE,
            title="繰り返し検知",
            detected_at=NOW,
        )
        for _ in range(50):
            intake.intake_from_alert(alert)

        other_alert = Alert.objects.create(
            project=self.project,
            category=Alert.Category.QUALITY,
            title="別の正常な検知",
            detected_at=NOW,
        )
        result = intake.intake_from_alert(other_alert)

        self.assertTrue(result.created)
        self.assertEqual(PmoWorkItem.objects.filter(tenant=self.tenant).count(), 2)
