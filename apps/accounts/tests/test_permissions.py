"""操作単位の認可判定テスト。

権限は「画面にボタンが出るか」ではなく「操作が通るか」で決まる。ここでは
判定関数だけを直接検証し、画面経由の検証は `projects/tests/test_project_members.py`
で行う（表示と POST の両方を塞げているかを分けて確かめるため）。
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts.constants import Action, ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.accounts.services import permissions
from apps.projects.models import Project, ProjectMember, Risk


def _user(email: str, *, tenant: Tenant | None, role: str) -> User:
    return User.objects.create_user(
        username=email, email=email, password="x", tenant=tenant, role=role
    )


class TenantRolePermissionTests(TestCase):
    """テナントロールごとに操作可否が変わること。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_参照ロールは閲覧だけできる(self):
        user = _user("viewer@example.com", tenant=self.tenant, role=Role.VIEWER)

        self.assertTrue(permissions.can(user, Action.VIEW))
        self.assertFalse(permissions.can(user, Action.EDIT))
        self.assertFalse(permissions.can(user, Action.APPROVE))
        self.assertFalse(permissions.can(user, Action.MANAGE))

    def test_変更管理者は編集できるが承認できない(self):
        user = _user("change@example.com", tenant=self.tenant, role=Role.CHANGE_MANAGER)

        self.assertTrue(permissions.can(user, Action.EDIT))
        self.assertFalse(permissions.can(user, Action.APPROVE))

    def test_PMOは承認できる(self):
        user = _user("pmo@example.com", tenant=self.tenant, role=Role.PMO)

        self.assertTrue(permissions.can(user, Action.APPROVE))
        self.assertFalse(permissions.can(user, Action.MANAGE))

    def test_テナント管理者は全操作できる(self):
        user = _user("ta@example.com", tenant=self.tenant, role=Role.TENANT_ADMIN)

        for action in Action.values:
            with self.subTest(action=action):
                self.assertTrue(permissions.can(user, action))

    def test_旧判定と新表が食い違っても承認権限は失われない(self):
        # `can_approve` が True のロールは、表を書き換えても承認できること。
        user = _user("q@example.com", tenant=self.tenant, role=Role.QUALITY_MANAGER)

        with self.settings(ROLE_PERMISSIONS={"quality": ("view",)}):
            self.assertTrue(user.can_approve)
            self.assertTrue(permissions.can(user, Action.APPROVE))

    def test_未認証は何もできない(self):
        for action in Action.values:
            with self.subTest(action=action):
                self.assertFalse(permissions.can(AnonymousUser(), action))


class ProjectRolePermissionTests(TestCase):
    """案件メンバーの役割で権限が変わること。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="beta", name="BETA")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

    def _member(self, email: str, *, role: str, project_role: str) -> User:
        user = _user(email, tenant=self.tenant, role=role)
        ProjectMember.objects.create(project=self.project, user=user, role=project_role)

        return user

    def test_案件のPMは管理までできる(self):
        user = self._member("pm@example.com", role=Role.VIEWER, project_role=ProjectRole.PROJECT_MANAGER)

        self.assertTrue(permissions.can(user, Action.MANAGE, self.project))
        self.assertTrue(permissions.can(user, Action.APPROVE, self.project))

    def test_案件の参照役割は編集できない(self):
        user = self._member("v@example.com", role=Role.PMO, project_role=ProjectRole.VIEWER)

        self.assertTrue(permissions.can(user, Action.VIEW, self.project))
        self.assertFalse(permissions.can(user, Action.EDIT, self.project))

    def test_案件単位の権限がテナント単位より優先される(self):
        # テナントロールでは承認できるが、案件では参照役割なので承認できない。
        user = self._member("p@example.com", role=Role.PMO, project_role=ProjectRole.VIEWER)

        self.assertTrue(permissions.can(user, Action.APPROVE))
        self.assertFalse(permissions.can(user, Action.APPROVE, self.project))

    def test_案件のメンバー役割は編集できるが承認できない(self):
        user = self._member("m@example.com", role=Role.PMO, project_role=ProjectRole.MEMBER)

        self.assertTrue(permissions.can(user, Action.EDIT, self.project))
        self.assertFalse(permissions.can(user, Action.APPROVE, self.project))

    def test_案件メンバーでなければ何もできない(self):
        user = _user("out@example.com", tenant=self.tenant, role=Role.PMO)

        self.assertFalse(permissions.can(user, Action.VIEW, self.project))

    def test_テナント管理者は案件役割に関わらず管理できる(self):
        user = _user("ta@example.com", tenant=self.tenant, role=Role.TENANT_ADMIN)
        ProjectMember.objects.create(project=self.project, user=user, role=ProjectRole.VIEWER)

        self.assertTrue(permissions.can(user, Action.MANAGE, self.project))

    def test_他テナントの案件には管理者でも触れない(self):
        user = _user("ta2@example.com", tenant=self.other_tenant, role=Role.TENANT_ADMIN)

        self.assertFalse(permissions.can(user, Action.VIEW, self.project))

    def test_案件配下のデータでも案件の権限で判定する(self):
        user = self._member("r@example.com", role=Role.PMO, project_role=ProjectRole.VIEWER)
        risk = Risk.objects.create(project=self.project, title="遅延リスク")

        self.assertTrue(permissions.can(user, Action.VIEW, risk))
        self.assertFalse(permissions.can(user, Action.EDIT, risk))

    def test_requireは権限がなければ例外を投げる(self):
        user = self._member("d@example.com", role=Role.VIEWER, project_role=ProjectRole.VIEWER)

        with self.assertRaises(PermissionDenied):
            permissions.require(user, Action.MANAGE, self.project)

    def test_メンバー権限一覧に役割と実効権限が並ぶ(self):
        self._member("pm2@example.com", role=Role.VIEWER, project_role=ProjectRole.PROJECT_MANAGER)
        self._member("v2@example.com", role=Role.VIEWER, project_role=ProjectRole.VIEWER)

        rows = permissions.member_permission_rows(self.project)

        self.assertEqual(len(rows), 2)
        by_role = {row.role: row for row in rows}
        self.assertTrue(by_role[ProjectRole.PROJECT_MANAGER].permissions.can_manage)
        self.assertFalse(by_role[ProjectRole.VIEWER].permissions.can_edit)
