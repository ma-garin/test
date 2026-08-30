"""画面の疎通テスト。

ナビゲーションに載っている画面がすべて 200 を返すことを確認する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.navigation import all_items


class HealthzTests(TestCase):
    def test_認証なしで200を返す(self):
        self.assertEqual(self.client.get(reverse("healthz")).status_code, 200)


class NavigationViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="admin-user",
            email="admin-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_ナビゲーションの全画面が表示できる(self):
        for item in all_items():
            with self.subTest(screen=item.key):
                response = self.client.get(reverse(item.url_name))

                self.assertEqual(response.status_code, 200)

    def test_未ログインならログイン画面へ誘導する(self):
        self.client.logout()
        response = self.client.get(reverse("performance:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
