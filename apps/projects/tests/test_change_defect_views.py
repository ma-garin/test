"""変更要求・不具合の CRUD と判断のテスト。

押さえるのは 2 点。
1. 他テナント・非参照案件のデータを編集・判断できないこと（404）
2. 判断（承認・却下）は権限のある利用者だけが実行でき、証跡が残ること
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.audit.models import OperationLog
from apps.projects.models import ChangeRequest, Defect, Project, ProjectMember


class ChangeDefectViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant_a = Tenant.objects.create(code="a", name="テナントA")
        self.tenant_b = Tenant.objects.create(code="b", name="テナントB")

        self.project_a = Project.objects.create(tenant=self.tenant_a, code="a1", name="A案件1")
        self.project_b = Project.objects.create(tenant=self.tenant_b, code="b1", name="B案件1")

        self.approver = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.PMO,
        )
        self.viewer = User.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.VIEWER,
        )
        # 案件内の役割はテナントロールと揃える。既定の「メンバー」のままだと
        # 案件役割が優先される判定（承認は PMO 以上）と食い違い、テストが
        # 実際の権限の切れ目を検証しなくなる。
        ProjectMember.objects.create(
            project=self.project_a, user=self.approver, role=ProjectRole.PMO
        )
        ProjectMember.objects.create(
            project=self.project_a, user=self.viewer, role=ProjectRole.VIEWER
        )

        self.change_a = ChangeRequest.objects.create(
            project=self.project_a,
            title="A案件の仕様変更",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )
        self.change_b = ChangeRequest.objects.create(
            project=self.project_b,
            title="B案件の仕様変更",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )
        self.defect_a = Defect.objects.create(project=self.project_a, title="A案件の不具合")
        self.defect_b = Defect.objects.create(project=self.project_b, title="B案件の不具合")

    # --- 変更要求 ---

    def test_変更要求を新規作成できる(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("projects:change_create"),
            {
                "project": str(self.project_a.pk),
                "title": "追加の帳票要件",
                "status": ChangeRequest.Status.DRAFT,
                "requested_by": "顧客",
                "description": "帳票を1本追加する",
                "impact_summary": "設計・実装・テストに影響",
                "impact_scope": "設計\n実装",
                "estimated_effort_days": "3.5",
                "schedule_impact_days": "2",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = ChangeRequest.objects.get(title="追加の帳票要件")
        self.assertEqual(created.project, self.project_a)
        self.assertEqual(created.impact_scope, ["設計", "実装"])

    def test_他テナントの案件を指定した変更要求は作成できない(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("projects:change_create"),
            {
                "project": str(self.project_b.pk),
                "title": "他テナントへの混入",
                "status": ChangeRequest.Status.DRAFT,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ChangeRequest.objects.filter(title="他テナントへの混入").exists())

    def test_変更要求を編集できる(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("projects:change_edit", args=[self.change_a.pk]),
            {
                "project": str(self.project_a.pk),
                "title": "A案件の仕様変更（修正）",
                "status": ChangeRequest.Status.UNDER_REVIEW,
                "impact_scope": "外部IF",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.change_a.refresh_from_db()
        self.assertEqual(self.change_a.title, "A案件の仕様変更（修正）")
        self.assertEqual(self.change_a.impact_scope, ["外部IF"])

    def test_他テナントの変更要求は編集できない(self):
        self.client.force_login(self.approver)

        self.assertEqual(
            self.client.get(reverse("projects:change_edit", args=[self.change_b.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("projects:change_decide", args=[self.change_b.pk])).status_code,
            404,
        )

    def test_承認権限のある利用者は判断でき証跡が残る(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("projects:change_decide", args=[self.change_a.pk]),
            {"decision": "approved", "reason": "影響工数が許容範囲のため承認"},
        )

        self.assertEqual(response.status_code, 302)
        self.change_a.refresh_from_db()
        self.assertEqual(self.change_a.status, ChangeRequest.Status.APPROVED)
        self.assertEqual(self.change_a.decided_by, self.approver)
        self.assertIsNotNone(self.change_a.decided_at)
        self.assertEqual(self.change_a.decision_reason, "影響工数が許容範囲のため承認")
        self.assertTrue(
            OperationLog.objects.filter(
                action="change_request.decide", user=self.approver, project=self.project_a
            ).exists()
        )

    def test_判断理由が空なら判断は記録されない(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("projects:change_decide", args=[self.change_a.pk]),
            {"decision": "rejected", "reason": "   "},
        )

        self.assertEqual(response.status_code, 200)
        self.change_a.refresh_from_db()
        self.assertEqual(self.change_a.status, ChangeRequest.Status.PENDING_APPROVAL)
        self.assertIsNone(self.change_a.decided_at)

    def test_承認権限のない利用者は判断できない(self):
        self.client.force_login(self.viewer)

        self.assertEqual(
            self.client.get(reverse("projects:change_decide", args=[self.change_a.pk])).status_code,
            403,
        )
        response = self.client.post(
            reverse("projects:change_decide", args=[self.change_a.pk]),
            {"decision": "approved", "reason": "権限がないのに承認"},
        )
        self.assertEqual(response.status_code, 403)
        self.change_a.refresh_from_db()
        self.assertEqual(self.change_a.status, ChangeRequest.Status.PENDING_APPROVAL)
        self.assertIsNone(self.change_a.decided_by)

    def test_一覧の判断ボタンは権限のある利用者にだけ出る(self):
        decide_url = reverse("projects:change_decide", args=[self.change_a.pk])

        self.client.force_login(self.viewer)
        self.assertNotContains(self.client.get(reverse("dashboard:change")), decide_url)

        self.client.force_login(self.approver)
        self.assertContains(self.client.get(reverse("dashboard:change")), decide_url)

    def test_未ログインは変更要求フォームへ入れない(self):
        response = self.client.get(reverse("projects:change_create"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response["Location"])

    # --- 不具合 ---

    def test_不具合を新規作成できる(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("projects:defect_create"),
            {
                "project": str(self.project_a.pk),
                "title": "検索結果が0件になる",
                "status": Defect.Status.NEW,
                "severity": "high",
                "phase": "結合テスト",
                "description": "条件指定時に0件",
                "detected_on": "2026-07-01",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = Defect.objects.get(title="検索結果が0件になる")
        self.assertEqual(created.project, self.project_a)
        self.assertEqual(created.phase, "結合テスト")

    def test_不具合を編集できる(self):
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("projects:defect_edit", args=[self.defect_a.pk]),
            {
                "project": str(self.project_a.pk),
                "title": "A案件の不具合（修正）",
                "status": Defect.Status.FIXING,
                "severity": "medium",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.defect_a.refresh_from_db()
        self.assertEqual(self.defect_a.status, Defect.Status.FIXING)

    def test_不具合をクローズしてもレコードは残る(self):
        self.client.force_login(self.approver)

        response = self.client.post(reverse("projects:defect_close", args=[self.defect_a.pk]))

        self.assertEqual(response.status_code, 302)
        self.defect_a.refresh_from_db()
        self.assertEqual(self.defect_a.status, Defect.Status.CLOSED)
        self.assertIsNotNone(self.defect_a.closed_on)
        self.assertTrue(Defect.objects.filter(pk=self.defect_a.pk).exists())

    def test_他テナントの不具合は編集もクローズもできない(self):
        self.client.force_login(self.approver)

        self.assertEqual(
            self.client.get(reverse("projects:defect_edit", args=[self.defect_b.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("projects:defect_close", args=[self.defect_b.pk])).status_code,
            404,
        )
        self.defect_b.refresh_from_db()
        self.assertEqual(self.defect_b.status, Defect.Status.NEW)

    def test_不具合一覧は自分が参照できる案件だけを表示する(self):
        self.client.force_login(self.approver)

        response = self.client.get(reverse("projects:defect_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A案件の不具合")
        self.assertNotContains(response, "B案件の不具合")
