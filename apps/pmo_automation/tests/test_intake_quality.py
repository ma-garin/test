"""services/intake_quality.py（品質相関のintake）を検証する。"""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_automation.models import PmoWorkItem, WorkKind
from apps.pmo_automation.services.intake_quality import intake_from_quality_gate_failure
from apps.projects.models import Defect, Issue, Project, QualityMetric

TODAY = timezone.localdate()


class IntakeQualityFactoryMixin:
    def _tenant(self, code: str = "acme") -> Tenant:
        return Tenant.objects.create(code=code, name=code.upper())

    def _project(self, tenant: Tenant, code: str = "p1") -> Project:
        return Project.objects.create(tenant=tenant, code=code, name="基幹刷新")

    def _metric(self, project: Project, **kwargs) -> QualityMetric:
        defaults = {
            "project": project,
            "measured_on": TODAY,
            "metric_key": "test_pass_rate",
            "metric_label": "テスト消化率",
            "value": 40,
            "threshold": 80,
            "higher_is_better": True,
        }
        defaults.update(kwargs)

        return QualityMetric.objects.create(**defaults)


class IntakeFromQualityGateFailureTests(IntakeQualityFactoryMixin, TestCase):
    def setUp(self) -> None:
        self.tenant = self._tenant()
        self.project = self._project(self.tenant)

    def test_ゲート未達のmetricからWork_Itemが1件作られる(self) -> None:
        Issue.objects.create(project=self.project, title="未解決課題", status=Issue.Status.OPEN)
        Defect.objects.create(project=self.project, title="未解決不具合", status=Defect.Status.NEW)
        metric = self._metric(self.project)

        result = intake_from_quality_gate_failure(metric)

        self.assertTrue(result.created)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
        self.assertEqual(result.work_item.kind, WorkKind.DATA_QUALITY_REPAIR)
        self.assertIn("未解決の課題 1 件", result.work_item.block_reason)
        self.assertIn("未解決の不具合 1 件", result.work_item.block_reason)

    def test_ゲート合格のmetricは対象外(self) -> None:
        metric = self._metric(self.project, value=90)

        with self.assertRaises(ValueError):
            intake_from_quality_gate_failure(metric)

        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_threshold未設定は判定不能として対象外(self) -> None:
        metric = self._metric(self.project, threshold=None)

        with self.assertRaises(ValueError):
            intake_from_quality_gate_failure(metric)

        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_dry_runは新規作成をDBに保存しない(self) -> None:
        metric = self._metric(self.project)

        result = intake_from_quality_gate_failure(metric, dry_run=True)

        self.assertTrue(result.created)
        self.assertIsNone(result.work_item)
        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_同じmetricを再度intakeしてもWork_Itemは増えない(self) -> None:
        metric = self._metric(self.project)

        first = intake_from_quality_gate_failure(metric)
        second = intake_from_quality_gate_failure(metric)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.work_item.pk, first.work_item.pk)
        self.assertEqual(PmoWorkItem.objects.count(), 1)
