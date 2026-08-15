"""成果物の生成・保存・承認に対する権限テスト。

承認は「根拠が足りているか」（`approval_service`）と「その人が決めてよいか」
（`permissions`）の 2 つのゲートを通す。既存のテストは前者しか見ておらず、
参照専用ロールが直接 POST すれば承認できる状態だった。ここでは後者を、
403 と「Approval が 1 件も増えない・状態が変わらない」の対で確かめる。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.pmo.models import Approval, Deliverable
from apps.projects.models import Project, ProjectMember


class PmoWritePermissionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

        self.viewer = self._member("viewer", Role.VIEWER, ProjectRole.VIEWER)
        # 編集はできるが承認はできない立場。
        self.member = self._member("member", Role.CHANGE_MANAGER, ProjectRole.MEMBER)

        self.deliverable = Deliverable.objects.create(
            project=self.project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title="週次報告",
            status=Deliverable.Status.PENDING_APPROVAL,
            ai_generated_body="今週は結合試験を実施しました。",
            body="今週は結合試験を実施しました。",
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

    def _assert_not_decided(self) -> None:
        self.deliverable.refresh_from_db()

        self.assertEqual(self.deliverable.status, Deliverable.Status.PENDING_APPROVAL)
        self.assertEqual(Approval.objects.count(), 0)

    # --- 承認 ---------------------------------------------------------------

    def test_参照専用ロールは承認できず証跡も増えない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("pmo:approvals"),
            {
                "deliverable": str(self.deliverable.pk),
                "decision": Approval.Decision.APPROVED,
                "comment": "権限がないのに承認",
            },
        )

        self.assertEqual(response.status_code, 403)
        self._assert_not_decided()

    def test_参照専用ロールは差戻もできない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("pmo:approvals"),
            {
                "deliverable": str(self.deliverable.pk),
                "decision": Approval.Decision.REJECTED,
                "comment": "権限がないのに差戻",
            },
        )

        self.assertEqual(response.status_code, 403)
        self._assert_not_decided()

    def test_編集はできても承認権限がなければ承認できない(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("pmo:approvals"),
            {
                "deliverable": str(self.deliverable.pk),
                "decision": Approval.Decision.APPROVED,
                "comment": "メンバーは承認者ではない",
            },
        )

        self.assertEqual(response.status_code, 403)
        self._assert_not_decided()

    def test_参照専用ロールでも承認画面は開ける(self):
        """締めるのは判断だけ。承認待ちの状況を見る導線は残す。"""

        self.client.force_login(self.viewer)

        self.assertEqual(self.client.get(reverse("pmo:approvals")).status_code, 200)

    # --- 生成・確定本文の保存 -----------------------------------------------

    def test_参照専用ロールは成果物を生成できず件数も増えない(self):
        self.client.force_login(self.viewer)

        before = Deliverable.objects.count()
        response = self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "generate",
                "project": str(self.project.pk),
                "generator": "weekly_report",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Deliverable.objects.count(), before)

    def test_参照専用ロールは確定本文を保存できない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(self.deliverable.pk),
                "title": "書き換えたタイトル",
                "body": "権限がないのに書き換えた本文",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.deliverable.refresh_from_db()
        self.assertEqual(self.deliverable.title, "週次報告")
        self.assertEqual(self.deliverable.body, "今週は結合試験を実施しました。")

    def test_編集できる立場なら確定本文を保存できる(self):
        """締めすぎていないこと。本文の確定は編集権限で行える。"""

        self.client.force_login(self.member)

        response = self.client.post(
            reverse("pmo:deliverables"),
            {
                "action": "save",
                "deliverable": str(self.deliverable.pk),
                "title": "週次報告",
                "body": "人が確認して直した本文",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.deliverable.refresh_from_db()
        self.assertEqual(self.deliverable.body, "人が確認して直した本文")
