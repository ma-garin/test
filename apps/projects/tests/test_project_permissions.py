"""案件単位の権限（要件 #30）。

守りたい性質は 1 つ。**案件ロールは権限を狭めるだけで、広げない。**
参照専用の利用者を案件責任者に任命したら承認できる、という抜け道を作らない。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import ChangeRequest, Project, ProjectMember, WbsTask
from apps.projects.permissions import (
    can_approve_in_project,
    can_edit_project,
    editable_projects_for,
    project_role,
)


class ProjectPermissionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _user(self, username: str, role: str) -> User:
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="x",
            tenant=self.tenant,
            role=role,
        )

    def _member(self, user: User, role: str) -> ProjectMember:
        return ProjectMember.objects.create(project=self.project, user=user, role=role)

    def test_メンバーでなければ案件ロールを持たない(self) -> None:
        user = self._user("outsider", Role.PMO)

        self.assertIsNone(project_role(user, self.project))
        self.assertFalse(can_edit_project(user, self.project))
        self.assertFalse(can_approve_in_project(user, self.project))

    def test_案件PMOは承認できる(self) -> None:
        user = self._user("pmo", Role.PMO)
        self._member(user, ProjectRole.PMO)

        self.assertTrue(can_approve_in_project(user, self.project))

    def test_担当は編集できるが承認はできない(self) -> None:
        user = self._user("member", Role.PMO)
        self._member(user, ProjectRole.MEMBER)

        self.assertTrue(can_edit_project(user, self.project))
        self.assertFalse(can_approve_in_project(user, self.project))

    def test_案件で参照のみなら編集も承認もできない(self) -> None:
        user = self._user("readonly", Role.PMO)
        self._member(user, ProjectRole.VIEWER)

        self.assertFalse(can_edit_project(user, self.project))
        self.assertFalse(can_approve_in_project(user, self.project))

    def test_案件ロールはテナントの権限を広げない(self) -> None:
        """テナント側が参照のみの人を案件責任者にしても、承認できてはいけない。"""

        user = self._user("viewer", Role.VIEWER)
        self._member(user, ProjectRole.OWNER)

        self.assertFalse(can_approve_in_project(user, self.project))
        self.assertFalse(can_edit_project(user, self.project))

    def test_テナント管理者はメンバーでなくても扱える(self) -> None:
        user = self._user("admin", Role.TENANT_ADMIN)

        self.assertEqual(project_role(user, self.project), ProjectRole.OWNER)
        self.assertTrue(can_edit_project(user, self.project))
        self.assertTrue(can_approve_in_project(user, self.project))

    def test_編集できる案件だけを選択肢に出す(self) -> None:
        other = Project.objects.create(tenant=self.tenant, code="p2", name="別案件")
        user = self._user("member", Role.PMO)
        self._member(user, ProjectRole.MEMBER)
        ProjectMember.objects.create(project=other, user=user, role=ProjectRole.VIEWER)

        editable = editable_projects_for(user, Project.objects.filter(tenant=self.tenant))

        self.assertEqual([project.code for project in editable], ["p1"])


class ProjectPermissionViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.membership = ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.VIEWER
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["tenant_id"] = str(self.tenant.pk)
        session.save()

        self.task = WbsTask.objects.create(
            project=self.project, wbs_code="1.1", name="設計", owner="担当"
        )
        self.change = ChangeRequest.objects.create(
            project=self.project,
            title="仕様変更",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )

    def test_参照のみの案件ではタスクを編集できない(self) -> None:
        response = self.client.get(reverse("projects:task_edit", args=[self.task.pk]))

        self.assertEqual(response.status_code, 403)

    def test_参照のみの案件では変更要求を判断できない(self) -> None:
        response = self.client.get(reverse("projects:change_decide", args=[self.change.pk]))

        self.assertEqual(response.status_code, 403)

    def test_担当に変えれば編集できる(self) -> None:
        ProjectMember.objects.filter(pk=self.membership.pk).update(role=ProjectRole.MEMBER)

        response = self.client.get(reverse("projects:task_edit", args=[self.task.pk]))

        self.assertEqual(response.status_code, 200)

    def test_参照のみでもタスク詳細は見られる(self) -> None:
        """権限を狭めるのは書き込みだけ。読めなくすると業務が回らない。"""

        response = self.client.get(reverse("projects:task_detail", args=[self.task.pk]))

        self.assertEqual(response.status_code, 200)

    def test_案件詳細にメンバーの案件ロールが出る(self) -> None:
        response = self.client.get(reverse("projects:detail", args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "参照のみ")
