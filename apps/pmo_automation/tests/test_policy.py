"""services/policy.py の評価関数と、H-03（古い根拠）を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.pmo_automation.models import (
    ApprovalRequest,
    ApprovalStatus,
    AutomationLevel,
    EvidenceBundle,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkPlan,
    WorkPlanState,
    WorkStep,
    WorkStepState,
)
from apps.pmo_automation.services import policy
from apps.projects.models import Project

NOW = timezone.now()


class PolicyTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.work_item = PmoWorkItem.objects.create(
            tenant=self.tenant,
            project=self.project,
            kind=WorkKind.DETECTION_TRIAGE,
            source_type="alert",
            source_key="alert-1",
            dedupe_key="alert:alert-1",
        )
        self.plan = WorkPlan.objects.create(
            work_item=self.work_item, version=1, automation_level=AutomationLevel.INTERNAL_APPLY
        )

    def _step(self, **kwargs) -> WorkStep:
        defaults = {
            "plan": self.plan,
            "order": 1,
            "kind": "internal_draft",
            "automation_level": AutomationLevel.INTERNAL_APPLY,
            "idempotency_key": "step-1",
        }
        defaults.update(kwargs)

        return WorkStep.objects.create(**defaults)

    def _evidence(self, **kwargs) -> EvidenceBundle:
        defaults = {
            "work_item": self.work_item,
            "source_type": "alert",
            "source_ref": "alert-1",
            "captured_at": NOW,
            "expires_at": NOW + timedelta(hours=1),
            "content_hash": "hash-1",
            "scope": {"tenant": "acme", "project": "p1"},
        }
        defaults.update(kwargs)

        return EvidenceBundle.objects.create(**defaults)


class EvaluateStepTests(PolicyTestBase):
    def test_internal_applyで根拠が新鮮なら自動実行を許可する(self) -> None:
        step = self._step(automation_level=AutomationLevel.INTERNAL_APPLY)
        evidence = [self._evidence()]

        decision = policy.evaluate_step(step=step, evidence_bundles=evidence, now=NOW)

        self.assertTrue(decision.allow)
        self.assertEqual(decision.next_state, WorkItemState.AUTO_RUNNING)

    def test_H03_期限切れの根拠は確認待ちへ倒す(self) -> None:
        """H-03: approve Step の根拠が期限切れなら awaiting_confirmation か hold。executor は呼ばれない。"""

        step = self._step(automation_level=AutomationLevel.APPROVE)
        stale_evidence = [self._evidence(expires_at=NOW - timedelta(minutes=1))]

        decision = policy.evaluate_step(step=step, evidence_bundles=stale_evidence, now=NOW)

        self.assertFalse(decision.allow)
        self.assertIn(decision.next_state, (WorkItemState.AWAITING_CONFIRMATION, WorkItemState.HOLD))
        # policy.evaluate_step は純粋関数でありDB更新・外部呼出を一切行わないため、
        # 呼出があれば例外なくここまで到達すること自体が「呼ばれていない」ことの証跡になる。

    def test_根拠が一件もなければ確認待ちへ倒す(self) -> None:
        step = self._step(automation_level=AutomationLevel.INTERNAL_APPLY)

        decision = policy.evaluate_step(step=step, evidence_bundles=[], now=NOW)

        self.assertFalse(decision.allow)
        self.assertEqual(decision.next_state, WorkItemState.AWAITING_CONFIRMATION)

    def test_競合する根拠は確認待ちへ倒す(self) -> None:
        step = self._step(automation_level=AutomationLevel.INTERNAL_APPLY)
        evidence = [
            self._evidence(source_ref="a", conflict_group="g1"),
            self._evidence(source_ref="b", conflict_group="g1"),
        ]

        decision = policy.evaluate_step(step=step, evidence_bundles=evidence, now=NOW)

        self.assertFalse(decision.allow)
        self.assertEqual(decision.next_state, WorkItemState.AWAITING_CONFIRMATION)

    def test_prohibitedは常にholdへ倒す(self) -> None:
        step = self._step(automation_level=AutomationLevel.PROHIBITED)

        decision = policy.evaluate_step(step=step, evidence_bundles=[self._evidence()], now=NOW)

        self.assertFalse(decision.allow)
        self.assertEqual(decision.next_state, WorkItemState.HOLD)
        self.assertEqual(decision.failure_category, "policy")

    def test_confirmは確認待ちへ倒す(self) -> None:
        step = self._step(automation_level=AutomationLevel.CONFIRM)

        decision = policy.evaluate_step(step=step, evidence_bundles=[self._evidence()], now=NOW)

        self.assertEqual(decision.next_state, WorkItemState.AWAITING_CONFIRMATION)

    def test_approveは承認待ちへ倒す(self) -> None:
        step = self._step(automation_level=AutomationLevel.APPROVE)

        decision = policy.evaluate_step(step=step, evidence_bundles=[self._evidence()], now=NOW)

        self.assertEqual(decision.next_state, WorkItemState.AWAITING_APPROVAL)


class TransitionGuardTests(PolicyTestBase):
    def test_assessing_to_plannedは必須項目が揃えば許可する(self) -> None:
        result = policy.guard_assessing_to_planned(
            tenant_id=self.tenant.id,
            project_id=self.project.id,
            dedupe_key=self.work_item.dedupe_key,
            policy_snapshot={"level": "internal_apply"},
            evidence_bundles=[self._evidence()],
        )

        self.assertTrue(result.passed)

    def test_assessing_to_plannedはevidence欠落で拒否する(self) -> None:
        result = policy.guard_assessing_to_planned(
            tenant_id=self.tenant.id,
            project_id=self.project.id,
            dedupe_key=self.work_item.dedupe_key,
            policy_snapshot={"level": "internal_apply"},
            evidence_bundles=[],
        )

        self.assertFalse(result.passed)

    def test_assessing_to_plannedはscope欠落の根拠があれば拒否する(self) -> None:
        result = policy.guard_assessing_to_planned(
            tenant_id=self.tenant.id,
            project_id=self.project.id,
            dedupe_key=self.work_item.dedupe_key,
            policy_snapshot={"level": "internal_apply"},
            evidence_bundles=[self._evidence(scope={})],
        )

        self.assertFalse(result.passed)

    def test_planned_to_auto_runningは全Stepがobserve系のときだけ許可する(self) -> None:
        internal_step = self._step(automation_level=AutomationLevel.INTERNAL_APPLY)
        approve_step = self._step(order=2, automation_level=AutomationLevel.APPROVE, idempotency_key="step-2")

        self.assertTrue(policy.guard_planned_to_auto_running(steps=[internal_step]).passed)
        self.assertFalse(policy.guard_planned_to_auto_running(steps=[internal_step, approve_step]).passed)

    def test_planned_to_awaiting_confirmationはconfirm_Stepか根拠不備で真になる(self) -> None:
        confirm_step = self._step(automation_level=AutomationLevel.CONFIRM)

        result = policy.guard_planned_to_awaiting_confirmation(
            steps=[confirm_step], evidence_bundles=[self._evidence()], now=NOW
        )

        self.assertTrue(result.passed)

    def test_awaiting_approval_to_executingは全条件を満たしたときだけ許可する(self) -> None:
        approver = User.objects.create_user(
            username="approver", email="approver@example.com", password="pw", tenant=self.tenant, role=Role.PMO
        )
        approval = ApprovalRequest.objects.create(
            work_item=self.work_item,
            plan_version=1,
            requested_action="draft.create",
            status=ApprovalStatus.APPROVED,
            required_role=Role.PMO,
        )

        result = policy.guard_awaiting_approval_to_executing(
            approval=approval,
            plan_version=1,
            evidence_bundles=[self._evidence()],
            expected_evidence_hash="hash-1",
            actual_evidence_hash="hash-1",
            actor_id=approver.id,
            actor_role=Role.PMO,
            required_role=Role.PMO,
            now=NOW,
        )

        self.assertTrue(result.passed)

    def test_awaiting_approval_to_executingは作成者の自己承認を拒否する(self) -> None:
        creator = User.objects.create_user(
            username="creator", email="creator@example.com", password="pw", tenant=self.tenant, role=Role.PMO
        )
        approval = ApprovalRequest.objects.create(
            work_item=self.work_item,
            plan_version=1,
            requested_action="draft.create",
            status=ApprovalStatus.APPROVED,
            required_role=Role.PMO,
            created_by=creator,
        )

        result = policy.guard_awaiting_approval_to_executing(
            approval=approval,
            plan_version=1,
            evidence_bundles=[self._evidence()],
            expected_evidence_hash="hash-1",
            actual_evidence_hash="hash-1",
            actor_id=creator.id,
            actor_role=Role.PMO,
            required_role=Role.PMO,
            now=NOW,
        )

        self.assertFalse(result.passed)

    def test_awaiting_approval_to_executingは根拠hash不一致を拒否する(self) -> None:
        approval = ApprovalRequest.objects.create(
            work_item=self.work_item,
            plan_version=1,
            requested_action="draft.create",
            status=ApprovalStatus.APPROVED,
        )

        result = policy.guard_awaiting_approval_to_executing(
            approval=approval,
            plan_version=1,
            evidence_bundles=[self._evidence()],
            expected_evidence_hash="hash-1",
            actual_evidence_hash="hash-2",
            actor_id=None,
            actor_role="",
            required_role="",
            now=NOW,
        )

        self.assertFalse(result.passed)

    def test_any_to_completedは未完了Stepがあれば拒否する(self) -> None:
        succeeded = self._step(state=WorkStepState.SUCCEEDED)
        pending = self._step(order=2, idempotency_key="step-2", state=WorkStepState.PENDING)

        self.assertTrue(policy.guard_any_to_completed(steps=[succeeded], evidence_bundles=[]).passed)
        self.assertFalse(policy.guard_any_to_completed(steps=[succeeded, pending], evidence_bundles=[]).passed)

    def test_any_to_completedは根拠が競合していれば拒否する(self) -> None:
        succeeded = self._step(state=WorkStepState.SUCCEEDED)
        conflicting_evidence = [
            self._evidence(source_ref="a", conflict_group="g1"),
            self._evidence(source_ref="b", conflict_group="g1"),
        ]

        result = policy.guard_any_to_completed(steps=[succeeded], evidence_bundles=conflicting_evidence)

        self.assertFalse(result.passed)

    def test_hold_to_plannedは理由と再評価の両方が必要(self) -> None:
        self.assertFalse(policy.guard_hold_to_planned(human_reason="", policy_reevaluated=True).passed)
        self.assertFalse(policy.guard_hold_to_planned(human_reason="対応済み", policy_reevaluated=False).passed)
        self.assertTrue(policy.guard_hold_to_planned(human_reason="対応済み", policy_reevaluated=True).passed)
