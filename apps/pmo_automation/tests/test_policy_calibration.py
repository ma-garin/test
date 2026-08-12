"""services/policy_calibration.py（PA-12: フィードバック較正のshadow評価）を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_automation.models import (
    ApprovalRequest,
    AutomationLevel,
    EvidenceBundle,
    ExecutionAttempt,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkPlan,
    WorkStep,
)
from apps.pmo_automation.services import policy_calibration
from apps.projects.models import Project

NOW = timezone.now()
PERIOD_START = NOW - timedelta(days=7)
PERIOD_END = NOW + timedelta(days=1)


class PolicyCalibrationTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _work_item(self, **kwargs) -> PmoWorkItem:
        defaults = {
            "tenant": self.tenant,
            "project": self.project,
            "kind": WorkKind.DATA_QUALITY_REPAIR,
            "source_type": "alert",
            "source_key": "1",
            "dedupe_key": f"calib:{kwargs.get('source_key', '1')}",
            "state": WorkItemState.AWAITING_CONFIRMATION,
        }
        defaults.update(kwargs)

        return PmoWorkItem.objects.create(**defaults)

    def _plan_with_step(self, work_item, *, automation_level: str, order: int = 1) -> WorkStep:
        plan = WorkPlan.objects.create(work_item=work_item, version=1, automation_level=automation_level)

        return WorkStep.objects.create(
            plan=plan,
            order=order,
            kind="confirmation_request",
            automation_level=automation_level,
            idempotency_key=f"{work_item.dedupe_key}:1:{order}",
        )

    def _evidence(self, work_item, **kwargs) -> EvidenceBundle:
        defaults = {
            "source_type": "alert",
            "source_ref": "ev-1",
            "scope": {"tenant": self.tenant.code},
            "content_hash": "hash-1",
            "captured_at": NOW,
            "expires_at": NOW + timedelta(hours=1),
        }
        defaults.update(kwargs)

        return EvidenceBundle.objects.create(work_item=work_item, **defaults)

    def _snapshot(self) -> tuple:
        return (
            PmoWorkItem.objects.count(),
            WorkPlan.objects.count(),
            WorkStep.objects.count(),
            EvidenceBundle.objects.count(),
            ApprovalRequest.objects.count(),
            ExecutionAttempt.objects.count(),
        )


class CalibratePolicyChangeTests(PolicyCalibrationTestBase):
    def test_confirmからinternal_applyへ変えると自動実行可能件数を数える(self) -> None:
        work_item = self._work_item(source_key="1")
        self._plan_with_step(work_item, automation_level=AutomationLevel.CONFIRM)
        self._evidence(work_item)

        result = policy_calibration.calibrate_policy_change(
            self.tenant,
            work_kind=WorkKind.DATA_QUALITY_REPAIR,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            current_automation_level=AutomationLevel.CONFIRM,
            candidate_automation_level=AutomationLevel.INTERNAL_APPLY,
            now=NOW,
        )

        self.assertEqual(result.total_work_items, 1)
        self.assertEqual(result.evaluated_step_count, 1)
        self.assertEqual(result.candidate_next_state_outcomes.get(WorkItemState.AUTO_RUNNING), 1)
        self.assertIn("自動実行可能になる見込み 1 件", result.delta_summary)

    def test_DBへは一切書き込まない(self) -> None:
        work_item = self._work_item(source_key="2")
        self._plan_with_step(work_item, automation_level=AutomationLevel.CONFIRM)
        self._evidence(work_item)

        before = self._snapshot()
        before_step_level = WorkStep.objects.get().automation_level

        policy_calibration.calibrate_policy_change(
            self.tenant,
            work_kind=WorkKind.DATA_QUALITY_REPAIR,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            current_automation_level=AutomationLevel.CONFIRM,
            candidate_automation_level=AutomationLevel.INTERNAL_APPLY,
            now=NOW,
        )

        after = self._snapshot()
        self.assertEqual(before, after)
        # DBから読み直しても元のautomation_levelのまま(メモリ上の書き換えが永続化されていない)。
        self.assertEqual(WorkStep.objects.get().automation_level, before_step_level)

    def test_期間外のWork_Itemは対象外(self) -> None:
        work_item = self._work_item(source_key="3", created_at=NOW - timedelta(days=30))
        PmoWorkItem.objects.filter(pk=work_item.pk).update(created_at=NOW - timedelta(days=30))
        self._plan_with_step(work_item, automation_level=AutomationLevel.CONFIRM)

        result = policy_calibration.calibrate_policy_change(
            self.tenant,
            work_kind=WorkKind.DATA_QUALITY_REPAIR,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            current_automation_level=AutomationLevel.CONFIRM,
            candidate_automation_level=AutomationLevel.INTERNAL_APPLY,
            now=NOW,
        )

        self.assertEqual(result.total_work_items, 0)
        self.assertEqual(result.evaluated_step_count, 0)

    def test_対象Stepが無ければ比較対象なしと報告する(self) -> None:
        self._work_item(source_key="4")

        result = policy_calibration.calibrate_policy_change(
            self.tenant,
            work_kind=WorkKind.DATA_QUALITY_REPAIR,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            current_automation_level=AutomationLevel.CONFIRM,
            candidate_automation_level=AutomationLevel.INTERNAL_APPLY,
            now=NOW,
        )

        self.assertEqual(result.evaluated_step_count, 0)
        self.assertIn("比較対象がありません", result.delta_summary)

    def test_根拠が競合していれば候補automation_levelでも確認待ちのまま(self) -> None:
        work_item = self._work_item(source_key="5")
        self._plan_with_step(work_item, automation_level=AutomationLevel.CONFIRM)
        self._evidence(work_item, source_ref="a", conflict_group="g1")
        self._evidence(work_item, source_ref="b", conflict_group="g1")

        result = policy_calibration.calibrate_policy_change(
            self.tenant,
            work_kind=WorkKind.DATA_QUALITY_REPAIR,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            current_automation_level=AutomationLevel.CONFIRM,
            candidate_automation_level=AutomationLevel.INTERNAL_APPLY,
            now=NOW,
        )

        self.assertEqual(result.candidate_next_state_outcomes.get(WorkItemState.AWAITING_CONFIRMATION), 1)
        self.assertIsNone(result.candidate_next_state_outcomes.get(WorkItemState.AUTO_RUNNING))
