"""管制ダッシュボードの書き込み・判断に対する権限テスト。

検知の実行はアラートと介入提案を作り、介入提案の判断は AI の提案の採否を
確定させる。どちらも「見えること」と「決めてよいこと」は別なので、
参照専用ロールから直接 POST して 403 になること、かつレコードが 1 件も
増減しないことを対で確かめる。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.audit.models import OperationLog
from apps.dashboard.models import Alert, InterventionProposal
from apps.projects.models import Project, ProjectMember, WbsTask


class DashboardWritePermissionTests(TestCase):
    def setUp(self) -> None:
        self.today = timezone.localdate()
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

        self.viewer = self._member("viewer", Role.VIEWER, ProjectRole.VIEWER)
        # 編集はできるが承認はできない立場。介入提案の判断が編集権限で
        # 通ってしまわないことの検証に使う。
        self.member = self._member("member", Role.CHANGE_MANAGER, ProjectRole.MEMBER)
        self.approver = self._member("pmo", Role.PMO, ProjectRole.PMO)

        # 検知が必ず 1 件は拾うよう、遅延した先行タスクを置く。
        delayed = WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="遅延タスク",
            planned_end=self.today - timedelta(days=30),
        )
        delayed.related_tasks.add(
            WbsTask.objects.create(project=self.project, wbs_code="1.2", name="後続")
        )

        self.proposal = InterventionProposal.objects.create(
            project=self.project,
            title="要員を1名追加する",
            recommended_action="来週から2名体制へ",
        )

    def _member(self, name: str, role: str, project_role: str) -> User:
        user = User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=role,
        )
        ProjectMember.objects.create(project=self.project, user=user, role=project_role)

        return user

    # --- 予兆検知 -----------------------------------------------------------

    def test_参照専用ロールは検知を実行できずアラートも増えない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(reverse("dashboard:detection_run"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(InterventionProposal.objects.count(), 1)  # setUp の 1 件のみ

    def test_編集できる立場なら検知を実行できる(self):
        """締めすぎていないこと。検知は編集権限で実行できる。"""

        self.client.force_login(self.member)

        response = self.client.post(reverse("dashboard:detection_run"))

        self.assertEqual(response.status_code, 302)
        self.assertGreaterEqual(Alert.objects.count(), 1)

    def test_参照専用ロールでも検知結果の一覧は見られる(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("dashboard:detection"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Alert.objects.count(), 0)

    # --- AI 介入提案の判断 ---------------------------------------------------

    def test_参照専用ロールは介入提案を判断できず証跡も残らない(self):
        self.client.force_login(self.viewer)

        before = OperationLog.objects.count()
        response = self.client.post(
            reverse("dashboard:intervention_decide", args=[self.proposal.pk]),
            {"status": "accepted", "decision_reason": "権限がないのに採用"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(OperationLog.objects.count(), before)

        saved = InterventionProposal.objects.get(pk=self.proposal.pk)
        self.assertEqual(saved.status, InterventionProposal.Status.PROPOSED)
        self.assertIsNone(saved.decided_by)

    def test_編集はできても承認権限がなければ判断できない(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("dashboard:intervention_decide", args=[self.proposal.pk]),
            {"status": "accepted", "decision_reason": "メンバーは判断者ではない"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            InterventionProposal.objects.get(pk=self.proposal.pk).status,
            InterventionProposal.Status.PROPOSED,
        )

    def test_参照専用ロールには判断フォームも見せない(self):
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("dashboard:intervention_decide", args=[self.proposal.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_承認権限があれば判断できる(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("dashboard:intervention_decide", args=[self.proposal.pk]),
            {"status": "accepted", "decision_reason": "増員の根拠に納得したため"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            InterventionProposal.objects.get(pk=self.proposal.pk).status,
            InterventionProposal.Status.ACCEPTED,
        )

    def test_判断はGETとPOST以外を受け付けない(self):
        """想定外のメソッドで判断処理へ入らせない。"""

        self.client.force_login(self.approver)

        response = self.client.delete(
            reverse("dashboard:intervention_decide", args=[self.proposal.pk])
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            InterventionProposal.objects.get(pk=self.proposal.pk).status,
            InterventionProposal.Status.PROPOSED,
        )
