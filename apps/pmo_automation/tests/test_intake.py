"""services/intake.py と、H-01（同一 Alert 再到着）を検証する。"""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from datetime import date

from django.contrib.contenttypes.models import ContentType

from apps.accounts.models import Tenant
from apps.dashboard.models import Alert
from apps.forecast.models import Confidence, ForecastSnapshot, Horizon, MissingInput
from apps.integrations.models import Connection, SyncJob
from apps.pmo_automation.models import EvidenceBundle, PmoWorkItem, WorkKind, WorkLink
from apps.pmo_automation.services import intake
from apps.projects.models import Project, WbsTask

NOW = timezone.now()


class IntakeFactoryMixin:
    def _tenant(self, code: str) -> Tenant:
        return Tenant.objects.create(code=code, name=code.upper())

    def _project(self, tenant: Tenant, code: str = "p1") -> Project:
        return Project.objects.create(tenant=tenant, code=code, name="基幹刷新")

    def _alert(self, project: Project, **kwargs) -> Alert:
        defaults = {
            "project": project,
            "category": Alert.Category.SCHEDULE,
            "title": "遅延の疑い",
            "detected_at": NOW,
        }
        defaults.update(kwargs)

        return Alert.objects.create(**defaults)


class IntakeFromAlertTests(IntakeFactoryMixin, TestCase):
    def setUp(self) -> None:
        self.tenant = self._tenant("acme")
        self.project = self._project(self.tenant)
        self.alert = self._alert(self.project)

    def test_新規Alertからは新しいWork_Itemが1件作られる(self) -> None:
        result = intake.intake_from_alert(self.alert)

        self.assertTrue(result.created)
        self.assertIsNotNone(result.work_item)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
        self.assertEqual(
            WorkLink.objects.filter(work_item=result.work_item, alert=self.alert).count(), 1
        )

    def test_H01_同一Alertを再度intakeしてもWork_Itemは増えない(self) -> None:
        """H-01: 同じ tenant/project/dedupe_key の有効 Work Item がある状態で、
        同じ Alert を再度 intake しても Work Item 総数は1のまま、WorkLinkだけ増える。"""

        first = intake.intake_from_alert(self.alert)

        second = intake.intake_from_alert(self.alert)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.work_item.pk, first.work_item.pk)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
        self.assertEqual(WorkLink.objects.filter(work_item=first.work_item, alert=self.alert).count(), 2)

    def test_dry_runは新規作成をDBに保存しない(self) -> None:
        result = intake.intake_from_alert(self.alert, dry_run=True)

        self.assertTrue(result.created)
        self.assertIsNone(result.work_item)
        self.assertEqual(PmoWorkItem.objects.count(), 0)
        self.assertEqual(WorkLink.objects.count(), 0)

    def test_既存Work_Itemがある状態でdry_runしてもWorkLinkを追加しない(self) -> None:
        created = intake.intake_from_alert(self.alert)

        result = intake.intake_from_alert(self.alert, dry_run=True)

        self.assertFalse(result.created)
        self.assertEqual(result.work_item.pk, created.work_item.pk)
        self.assertEqual(WorkLink.objects.filter(alert=self.alert).count(), 1)

    def test_dedupe_keyはsource_typeとsource_keyから決定的に導かれる(self) -> None:
        expected = f"alert:{self.alert.pk}"

        result = intake.intake_from_alert(self.alert)

        self.assertEqual(result.dedupe_key, expected)
        self.assertEqual(result.work_item.dedupe_key, expected)
        self.assertEqual(result.work_item.source_type, "alert")
        self.assertEqual(result.work_item.source_key, str(self.alert.pk))


class TenantBoundaryTests(IntakeFactoryMixin, TestCase):
    """H-01 safety_assertion: 別テナントの Work Item は変わらない。"""

    def setUp(self) -> None:
        self.tenant_a = self._tenant("tenant-a")
        self.tenant_b = self._tenant("tenant-b")
        self.project_a = self._project(self.tenant_a, code="p-a")
        self.project_b = self._project(self.tenant_b, code="p-b")
        self.alert_b = self._alert(self.project_b, title="B側の遅延")
        self.work_item_b = intake.intake_from_alert(self.alert_b).work_item

    def test_別テナントのAlertをintakeしてもtenant_Bは変わらない(self) -> None:
        alert_a = self._alert(self.project_a, title="A側の遅延")

        intake.intake_from_alert(alert_a)

        self.work_item_b.refresh_from_db()
        self.assertEqual(PmoWorkItem.objects.filter(tenant=self.tenant_b).count(), 1)
        self.assertEqual(WorkLink.objects.filter(work_item=self.work_item_b).count(), 1)
        self.assertEqual(PmoWorkItem.objects.filter(tenant=self.tenant_a).count(), 1)

class IntakeFromIntegrationJobFailureTests(IntakeFactoryMixin, TestCase):
    def setUp(self) -> None:
        self.tenant = self._tenant("acme")
        self.project = self._project(self.tenant)
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider="jira",
            name="Jira本番",
        )

    def _sync_job(self, **kwargs) -> SyncJob:
        defaults = {"connection": self.connection, "status": SyncJob.Status.FAILED}
        defaults.update(kwargs)

        return SyncJob.objects.create(**defaults)

    def test_同期失敗から新しいWork_Itemが1件作られる(self) -> None:
        sync_job = self._sync_job()

        result = intake.intake_from_integration_job_failure(sync_job)

        self.assertTrue(result.created)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
        self.assertEqual(result.work_item.tenant_id, self.tenant.id)
        self.assertEqual(result.work_item.project_id, self.project.id)
        self.assertEqual(
            WorkLink.objects.filter(work_item=result.work_item, integration_job=sync_job).count(), 1
        )

    def test_同じSyncJobを再度intakeしてもWork_Itemは増えない(self) -> None:
        sync_job = self._sync_job()

        first = intake.intake_from_integration_job_failure(sync_job)
        second = intake.intake_from_integration_job_failure(sync_job)

        self.assertFalse(second.created)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
        self.assertEqual(
            WorkLink.objects.filter(work_item=first.work_item, integration_job=sync_job).count(), 2
        )

    def test_dry_runは新規作成をDBに保存しない(self) -> None:
        sync_job = self._sync_job()

        result = intake.intake_from_integration_job_failure(sync_job, dry_run=True)

        self.assertIsNone(result.work_item)
        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_成功したSyncJobはintake対象外(self) -> None:
        sync_job = self._sync_job(status=SyncJob.Status.SUCCEEDED)

        with self.assertRaises(ValueError):
            intake.intake_from_integration_job_failure(sync_job)

    def test_project未設定の接続はintake対象外(self) -> None:
        tenant_wide_connection = Connection.objects.create(
            tenant=self.tenant, project=None, provider="jira", name="Jira全社共通"
        )
        sync_job = self._sync_job(connection=tenant_wide_connection)

        with self.assertRaises(ValueError):
            intake.intake_from_integration_job_failure(sync_job)


class IntakeFromForecastUndeterminableTests(IntakeFactoryMixin, TestCase):
    def setUp(self) -> None:
        self.tenant = self._tenant("acme")
        self.project = self._project(self.tenant)
        self.task = WbsTask.objects.create(
            project=self.project,
            wbs_code="A",
            name="結合試験",
            status=WbsTask.Status.IN_PROGRESS,
        )

    def _undeterminable_snapshot(self, **kwargs) -> ForecastSnapshot:
        defaults = {
            "project": self.project,
            "target_content_type": ContentType.objects.get_for_model(WbsTask),
            "target_object_id": self.task.pk,
            "as_of": NOW,
            "horizon": Horizon.TWO_DAYS,
            "confidence": Confidence.UNKNOWN,
            "missing_inputs": [MissingInput.NO_CALENDAR],
        }
        defaults.update(kwargs)

        return ForecastSnapshot.objects.create(**defaults)

    def test_H13_算定不能はdata_quality_repairのWork_Itemになる(self) -> None:
        """H-13: 勤務カレンダー未設定で算定不能な ForecastSnapshot を intake すると、
        data_quality_repair の Work Item が作られ、必要入力と影響範囲を持つ。"""

        snapshot = self._undeterminable_snapshot()

        result = intake.intake_from_forecast_undeterminable(snapshot)

        self.assertTrue(result.created)
        self.assertEqual(result.work_item.kind, WorkKind.DATA_QUALITY_REPAIR)
        self.assertIn("勤務カレンダー未設定", result.work_item.block_reason)

        evidence = result.work_item.evidence_bundles.get()
        self.assertEqual(evidence.scope["missing_inputs"], ["no_calendar"])
        self.assertEqual(evidence.scope["target_id"], str(self.task.pk))
        self.assertEqual(evidence.scope["target_label"], str(self.task))

    def test_勝手に値を補完して保存しない(self) -> None:
        """safety_assertion: 予測日・営業日数を推測して保存しない。"""

        snapshot = self._undeterminable_snapshot()

        result = intake.intake_from_forecast_undeterminable(snapshot)

        evidence = result.work_item.evidence_bundles.get()
        self.assertNotIn("forecast_date", evidence.scope)
        self.assertNotIn("variance_business_days", evidence.scope)

    def test_算定不能でないsnapshotはValueErrorになる(self) -> None:
        snapshot = self._undeterminable_snapshot(
            confidence=Confidence.HIGH,
            forecast_date=date(2026, 9, 1),
            variance_business_days=0,
            missing_inputs=[],
        )

        with self.assertRaises(ValueError):
            intake.intake_from_forecast_undeterminable(snapshot)

    def test_同一snapshotを再度intakeしてもWork_Itemは増えない(self) -> None:
        snapshot = self._undeterminable_snapshot()

        first = intake.intake_from_forecast_undeterminable(snapshot)
        second = intake.intake_from_forecast_undeterminable(snapshot)

        self.assertFalse(second.created)
        self.assertEqual(second.work_item.pk, first.work_item.pk)
        self.assertEqual(PmoWorkItem.objects.count(), 1)

    def test_dry_runは新規作成をDBに保存しない(self) -> None:
        snapshot = self._undeterminable_snapshot()

        result = intake.intake_from_forecast_undeterminable(snapshot, dry_run=True)

        self.assertIsNone(result.work_item)
        self.assertEqual(PmoWorkItem.objects.count(), 0)
        self.assertEqual(EvidenceBundle.objects.count(), 0)
