"""views.py と、H-14（承認センターの操作）を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.pmo_automation.models import (
    ApprovalRequest,
    ApprovalStatus,
    AutomationLevel,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkPlan,
)
from apps.projects.models import Project, ProjectMember

NOW = timezone.now()


class ViewTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.approver = User.objects.create_user(
            username="approver", email="approver@example.com", password="pw", tenant=self.tenant, role=Role.PMO
        )
        ProjectMember.objects.create(project=self.project, user=self.approver)
        self.viewer = User.objects.create_user(
            username="viewer", email="viewer@example.com", password="pw", tenant=self.tenant, role=Role.VIEWER
        )
        ProjectMember.objects.create(project=self.project, user=self.viewer)

    def _work_item(self, **kwargs) -> PmoWorkItem:
        defaults = {
            "tenant": self.tenant,
            "project": self.project,
            "kind": WorkKind.DETECTION_TRIAGE,
            "source_type": "alert",
            "source_key": "alert-1",
            "dedupe_key": "alert:alert-1",
            "state": WorkItemState.AWAITING_APPROVAL,
        }
        defaults.update(kwargs)

        return PmoWorkItem.objects.create(**defaults)

    def _plan(self, work_item, **kwargs) -> WorkPlan:
        defaults = {"work_item": work_item, "version": 1, "automation_level": AutomationLevel.APPROVE}
        defaults.update(kwargs)

        return WorkPlan.objects.create(**defaults)

    def _approval(self, work_item, **kwargs) -> ApprovalRequest:
        defaults = {
            "work_item": work_item,
            "plan_version": 1,
            "requested_action": "draft.create",
            "status": ApprovalStatus.PENDING,
        }
        defaults.update(kwargs)

        return ApprovalRequest.objects.create(**defaults)


class ApprovalCenterViewTests(ViewTestBase):
    def test_確認待ち承認待ち保留のWork_Itemが一覧に出る(self) -> None:
        self._work_item(state=WorkItemState.AWAITING_CONFIRMATION, dedupe_key="a:1")
        self._work_item(state=WorkItemState.AWAITING_APPROVAL, dedupe_key="a:2")
        self._work_item(state=WorkItemState.HOLD, dedupe_key="a:3", is_active=False)
        self._work_item(state=WorkItemState.COMPLETED, dedupe_key="a:4", is_active=False)

        self.client.force_login(self.approver)
        response = self.client.get(reverse("pmo_automation:approval_center"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["work_items"]), 3)

    def test_未ログインはログイン画面へリダイレクトされる(self) -> None:
        response = self.client.get(reverse("pmo_automation:approval_center"))

        self.assertEqual(response.status_code, 302)


class WorkItemDetailViewTests(ViewTestBase):
    def test_承認パケット7節が固定順で表示される(self) -> None:
        work_item = self._work_item()
        self._plan(work_item)
        self._approval(work_item)

        self.client.force_login(self.approver)
        response = self.client.get(reverse("pmo_automation:work_item_detail", args=[work_item.pk]))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for anchor in ["section-1", "section-2", "section-3", "section-4", "section-5", "section-6", "section-7"]:
            self.assertIn(anchor, content)

    def test_理由が無ければ送信できない(self) -> None:
        work_item = self._work_item()
        approval = self._approval(work_item)

        self.client.force_login(self.approver)
        self.client.post(
            reverse("pmo_automation:work_item_detail", args=[work_item.pk]),
            {"approval": str(approval.pk), "decision": ApprovalStatus.APPROVED, "reason": "", "plan_version": "1"},
        )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_権限が無いロールは承認できない(self) -> None:
        work_item = self._work_item()
        approval = self._approval(work_item)

        self.client.force_login(self.viewer)
        self.client.post(
            reverse("pmo_automation:work_item_detail", args=[work_item.pk]),
            {
                "approval": str(approval.pk),
                "decision": ApprovalStatus.APPROVED,
                "reason": "内容確認済み",
                "plan_version": "1",
            },
        )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_required_roleと異なるロールでは決定できない(self) -> None:
        """APPROVER_ROLESには入っていても、この承認個別のrequired_roleと
        一致しなければ拒否する（レビュー指摘対応）。"""

        work_item = self._work_item()
        approval = self._approval(work_item, required_role=Role.CHANGE_MANAGER)

        self.client.force_login(self.approver)  # approver は Role.PMO
        self.client.post(
            reverse("pmo_automation:work_item_detail", args=[work_item.pk]),
            {
                "approval": str(approval.pk),
                "decision": ApprovalStatus.APPROVED,
                "reason": "内容確認済み",
                "plan_version": "1",
            },
        )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_H14_理由と権限が揃えば承認でき状態と監査が表示される(self) -> None:
        work_item = self._work_item()
        self._plan(work_item)
        approval = self._approval(work_item)

        self.client.force_login(self.approver)
        response = self.client.post(
            reverse("pmo_automation:work_item_detail", args=[work_item.pk]),
            {
                "approval": str(approval.pk),
                "decision": ApprovalStatus.APPROVED,
                "reason": "内容確認済み",
                "plan_version": "1",
            },
            follow=True,
        )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.APPROVED)
        self.assertEqual(approval.decided_by_id, self.approver.id)
        self.assertContains(response, "内容確認済み")

    def test_H14_失効済みの承認は決定できない(self) -> None:
        work_item = self._work_item()
        approval = self._approval(work_item, status=ApprovalStatus.EXPIRED)

        self.client.force_login(self.approver)
        self.client.post(
            reverse("pmo_automation:work_item_detail", args=[work_item.pk]),
            {
                "approval": str(approval.pk),
                "decision": ApprovalStatus.APPROVED,
                "reason": "内容確認済み",
                "plan_version": "1",
            },
        )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.EXPIRED)

    def test_H14_plan版が変わっていると拒否される(self) -> None:
        work_item = self._work_item()
        self._plan(work_item, version=2)
        approval = self._approval(work_item, plan_version=1)

        self.client.force_login(self.approver)
        self.client.post(
            reverse("pmo_automation:work_item_detail", args=[work_item.pk]),
            {
                "approval": str(approval.pk),
                "decision": ApprovalStatus.APPROVED,
                "reason": "内容確認済み",
                "plan_version": "1",
            },
        )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_作成者本人は自己承認できない(self) -> None:
        work_item = self._work_item()
        approval = self._approval(work_item, created_by=self.approver)

        self.client.force_login(self.approver)
        self.client.post(
            reverse("pmo_automation:work_item_detail", args=[work_item.pk]),
            {
                "approval": str(approval.pk),
                "decision": ApprovalStatus.APPROVED,
                "reason": "内容確認済み",
                "plan_version": "",
            },
        )

        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_保留状態のWork_Itemも詳細を開ける(self) -> None:
        """H-14: 保留状態を含む4状態(確認待ち/承認待ち/失効/保留)のうち、保留のケース。"""

        work_item = self._work_item(state=WorkItemState.HOLD, is_active=False, block_reason="根拠不足")

        self.client.force_login(self.approver)
        response = self.client.get(reverse("pmo_automation:work_item_detail", args=[work_item.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "根拠不足")

    def test_他テナントのWork_Itemは404になる(self) -> None:
        other_tenant = Tenant.objects.create(code="other", name="Other")
        other_project = Project.objects.create(tenant=other_tenant, code="p2", name="別案件")
        other_work_item = PmoWorkItem.objects.create(
            tenant=other_tenant,
            project=other_project,
            kind=WorkKind.DETECTION_TRIAGE,
            source_type="alert",
            source_key="x",
            dedupe_key="alert:x",
        )

        self.client.force_login(self.approver)
        response = self.client.get(reverse("pmo_automation:work_item_detail", args=[other_work_item.pk]))

        self.assertEqual(response.status_code, 404)
