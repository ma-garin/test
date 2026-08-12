"""services/workflow.py の状態機械・承認失効(H-07)・職務分掌(H-08)を検証する。"""

from __future__ import annotations

import json
from pathlib import Path

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
)
from apps.pmo_automation.services import workflow
from apps.projects.models import Project

NOW = timezone.now()
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "docs" / "agent" / "pmo_autopilot_contract.json"


class WorkflowTestBase(TestCase):
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
            work_item=self.work_item, version=1, automation_level=AutomationLevel.APPROVE
        )

    def _user(self, username: str, *, role: str = Role.PMO) -> User:
        return User.objects.create_user(
            username=username, email=f"{username}@example.com", password="pw", tenant=self.tenant, role=role
        )

    def _evidence(self, **kwargs) -> EvidenceBundle:
        defaults = {
            "work_item": self.work_item,
            "source_type": "alert",
            "source_ref": "alert-1",
            "captured_at": NOW,
            "content_hash": "hash-1",
            "scope": {"tenant": "acme", "project": "p1"},
        }
        defaults.update(kwargs)

        return EvidenceBundle.objects.create(**defaults)

    def _approval(self, **kwargs) -> ApprovalRequest:
        defaults = {
            "work_item": self.work_item,
            "plan_version": 1,
            "requested_action": "slack.create_draft",
        }
        defaults.update(kwargs)

        return ApprovalRequest.objects.create(**defaults)


class TransitionWorkItemTests(WorkflowTestBase):
    def test_許可された遷移は成功する(self) -> None:
        workflow.transition_work_item(self.work_item, WorkItemState.ASSESSING)

        self.work_item.refresh_from_db()
        self.assertEqual(self.work_item.state, WorkItemState.ASSESSING)

    def test_許可されない遷移はTransitionErrorになる(self) -> None:
        with self.assertRaises(workflow.TransitionError):
            workflow.transition_work_item(self.work_item, WorkItemState.COMPLETED)

        self.work_item.refresh_from_db()
        self.assertEqual(self.work_item.state, WorkItemState.NEW)

    def test_terminal_stateに入るとis_activeがFalseになる(self) -> None:
        workflow.transition_work_item(self.work_item, WorkItemState.ASSESSING)
        workflow.transition_work_item(self.work_item, WorkItemState.HOLD)

        self.work_item.refresh_from_db()
        self.assertFalse(self.work_item.is_active)

    def test_holdからplannedに戻るとis_activeがTrueに戻る(self) -> None:
        workflow.transition_work_item(self.work_item, WorkItemState.ASSESSING)
        workflow.transition_work_item(self.work_item, WorkItemState.HOLD)

        workflow.transition_work_item(self.work_item, WorkItemState.PLANNED)

        self.work_item.refresh_from_db()
        self.assertTrue(self.work_item.is_active)
        self.assertEqual(self.work_item.state, WorkItemState.PLANNED)

    def test_ALLOWED_TRANSITIONSは契約ファイルと一致する(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        contract_transitions = {key: tuple(value) for key, value in contract["allowed_transitions"].items()}

        self.assertEqual(workflow.ALLOWED_TRANSITIONS, contract_transitions)


class ApprovalExpiryTests(WorkflowTestBase):
    """H-07: 承認失効。"""

    def test_H07_plan版が変わると承認は失効する(self) -> None:
        approval = self._approval(status=ApprovalStatus.APPROVED, plan_version=1)

        expired = workflow.expire_approval_if_stale(
            approval, current_plan_version=2, approved_evidence_hash="h1", current_evidence_hash="h1"
        )

        approval.refresh_from_db()
        self.assertTrue(expired)
        self.assertEqual(approval.status, ApprovalStatus.EXPIRED)

    def test_H07_根拠hashが変わると承認は失効する(self) -> None:
        approval = self._approval(status=ApprovalStatus.APPROVED, plan_version=1)

        expired = workflow.expire_approval_if_stale(
            approval, current_plan_version=1, approved_evidence_hash="h1", current_evidence_hash="h2"
        )

        approval.refresh_from_db()
        self.assertTrue(expired)
        self.assertEqual(approval.status, ApprovalStatus.EXPIRED)

    def test_policy版が変わると承認は失効する(self) -> None:
        approval = self._approval(status=ApprovalStatus.APPROVED, plan_version=1)

        expired = workflow.expire_approval_if_stale(
            approval,
            current_plan_version=1,
            approved_evidence_hash="h1",
            current_evidence_hash="h1",
            approved_policy_version=1,
            current_policy_version=2,
        )

        self.assertTrue(expired)

    def test_何も変わらなければ失効しない(self) -> None:
        approval = self._approval(status=ApprovalStatus.APPROVED, plan_version=1)

        expired = workflow.expire_approval_if_stale(
            approval, current_plan_version=1, approved_evidence_hash="h1", current_evidence_hash="h1"
        )

        approval.refresh_from_db()
        self.assertFalse(expired)
        self.assertEqual(approval.status, ApprovalStatus.APPROVED)

    def test_失効後の承認はexecuting遷移ガードを通らない(self) -> None:
        """safety_assertions: v1時点の承認で実行できない。"""

        approval = self._approval(status=ApprovalStatus.APPROVED, plan_version=1)
        workflow.expire_approval_if_stale(
            approval, current_plan_version=2, approved_evidence_hash="h1", current_evidence_hash="h1"
        )
        approval.refresh_from_db()

        result = workflow.can_execute(
            approval,
            plan_version=2,
            evidence_bundles=[self._evidence()],
            expected_evidence_hash="h1",
            actual_evidence_hash="h1",
            actor_id=None,
            actor_role="",
            required_role="",
            now=NOW,
        )

        self.assertFalse(result.passed)


class SelfApprovalTests(WorkflowTestBase):
    """H-08: 職務分掌。"""

    def test_H08_作成者は自己承認できない(self) -> None:
        creator = self._user("creator")
        approval = self._approval(created_by=creator)

        with self.assertRaises(workflow.SelfApprovalError):
            workflow.decide_approval(
                approval,
                actor_id=creator.id,
                decision=ApprovalStatus.APPROVED,
                decided_by=creator,
                decision_reason="自分で承認",
                now=NOW,
            )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_H08_最後の実行者は自己承認できない(self) -> None:
        executor = self._user("executor")
        approval = self._approval(last_executed_by=executor)

        with self.assertRaises(workflow.SelfApprovalError):
            workflow.decide_approval(
                approval,
                actor_id=executor.id,
                decision=ApprovalStatus.APPROVED,
                decided_by=executor,
                decision_reason="自分で承認",
                now=NOW,
            )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_第三者は承認できる(self) -> None:
        creator = self._user("creator2")
        approver = self._user("approver2")
        approval = self._approval(created_by=creator)

        result = workflow.decide_approval(
            approval,
            actor_id=approver.id,
            decision=ApprovalStatus.APPROVED,
            decided_by=approver,
            decision_reason="内容確認済み",
            now=NOW,
        )

        self.assertEqual(result.status, ApprovalStatus.APPROVED)
        self.assertEqual(result.decided_by_id, approver.id)

    def test_pending以外の承認は決定できない(self) -> None:
        approval = self._approval(status=ApprovalStatus.EXPIRED)

        with self.assertRaises(ValueError):
            workflow.decide_approval(
                approval,
                actor_id=None,
                decision=ApprovalStatus.APPROVED,
                decided_by=None,
                decision_reason="",
                now=NOW,
            )

    def test_EXPIREDやPENDINGはdecisionとして受け付けない(self) -> None:
        approval = self._approval()

        with self.assertRaises(ValueError):
            workflow.decide_approval(
                approval,
                actor_id=None,
                decision=ApprovalStatus.EXPIRED,
                decided_by=None,
                decision_reason="",
                now=NOW,
            )
