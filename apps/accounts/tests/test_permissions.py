"""操作単位の認可判定テスト。

権限は「画面にボタンが出るか」ではなく「操作が通るか」で決まる。
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from apps.accounts.constants import Action, Role
from apps.accounts.models import Tenant, User
from apps.accounts.services import permissions


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
