"""案件メンバー管理画面のテスト。

**画面で隠した操作が POST でも拒否されること**を必ず確かめる。カードを
出さないだけでは、URL を直接叩かれたときに素通りするため。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project, ProjectMember


class ProjectMemberViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="beta", name="BETA")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

        self.admin = self._user("admin@example.com", self.tenant, Role.TENANT_ADMIN)
        self.member = self._user("member@example.com", self.tenant, Role.PMO)
        self.candidate = self._user("cand@example.com", self.tenant, Role.VIEWER)
        self.outsider = self._user("out@example.com", self.other_tenant, Role.PMO)

        ProjectMember.objects.create(
            project=self.project, user=self.member, role=ProjectRole.MEMBER
        )

    def _user(self, email: str, tenant: Tenant, role: str) -> User:
        return User.objects.create_user(
            username=email, email=email, password="x", tenant=tenant, role=role
        )

    def _save_url(self) -> str:
        return reverse("projects:member_save", args=[self.project.pk])

    def _remove_url(self) -> str:
        return reverse("projects:member_remove", args=[self.project.pk])

    # --- 表示 ---------------------------------------------------------------

    def test_案件詳細にメンバーと権限が出る(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("projects:detail", args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "メンバーと権限")
        self.assertTrue(response.context["can_manage_members"])
        self.assertEqual(len(response.context["member_rows"]), 1)

    def test_権限がなければ登録フォームを出さない(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("projects:detail", args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage_members"])
        self.assertIsNone(response.context["member_form"])

    # --- POST 側の防御 ------------------------------------------------------

    def test_画面で隠した登録操作はPOSTでも拒否される(self):
        self.client.force_login(self.member)
        response = self.client.post(
            self._save_url(),
            {"user": str(self.candidate.pk), "role": ProjectRole.PMO, "role_label": ""},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectMember.objects.filter(project=self.project, user=self.candidate).exists()
        )

    def test_画面で隠した解除操作はPOSTでも拒否される(self):
        membership = ProjectMember.objects.get(project=self.project, user=self.member)
        self.client.force_login(self.member)
        response = self.client.post(self._remove_url(), {"member": str(membership.pk)})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectMember.objects.filter(pk=membership.pk).exists())

    def test_案件のPMはメンバーを登録できる(self):
        pm = self._user("pm@example.com", self.tenant, Role.VIEWER)
        ProjectMember.objects.create(
            project=self.project, user=pm, role=ProjectRole.PROJECT_MANAGER
        )
        self.client.force_login(pm)
        response = self.client.post(
            self._save_url(),
            {"user": str(self.candidate.pk), "role": ProjectRole.VIEWER, "role_label": ""},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ProjectMember.objects.filter(project=self.project, user=self.candidate).exists()
        )

    # --- 登録・変更・解除 ---------------------------------------------------

    def test_テナント管理者はメンバーを追加できる(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self._save_url(),
            {"user": str(self.candidate.pk), "role": ProjectRole.PMO, "role_label": "PMO担当"},
        )

        self.assertEqual(response.status_code, 302)
        created = ProjectMember.objects.get(project=self.project, user=self.candidate)
        self.assertEqual(created.role, ProjectRole.PMO)
        self.assertEqual(created.role_label, "PMO担当")

    def test_既存メンバーの役割を変更できる(self):
        self.client.force_login(self.admin)
        self.client.post(
            self._save_url(),
            {"user": str(self.member.pk), "role": ProjectRole.PROJECT_MANAGER, "role_label": ""},
        )
        self.member.refresh_from_db()
        membership = ProjectMember.objects.get(project=self.project, user=self.member)

        self.assertEqual(membership.role, ProjectRole.PROJECT_MANAGER)
        self.assertEqual(
            ProjectMember.objects.filter(project=self.project, user=self.member).count(), 1
        )

    def test_他テナントの利用者は選択肢にも入らず登録もできない(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self._save_url(),
            {"user": str(self.outsider.pk), "role": ProjectRole.MEMBER, "role_label": ""},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ProjectMember.objects.filter(project=self.project, user=self.outsider).exists()
        )

    def test_メンバーを解除できる(self):
        membership = ProjectMember.objects.get(project=self.project, user=self.member)
        self.client.force_login(self.admin)
        response = self.client.post(self._remove_url(), {"member": str(membership.pk)})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectMember.objects.filter(pk=membership.pk).exists())

    def test_他テナントの案件は存在しないものとして扱う(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            self._save_url(),
            {"user": str(self.candidate.pk), "role": ProjectRole.MEMBER, "role_label": ""},
        )

        self.assertEqual(response.status_code, 404)

    def test_GETでは状態を変えない(self):
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(self._save_url()).status_code, 405)
