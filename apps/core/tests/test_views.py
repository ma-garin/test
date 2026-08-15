"""画面の疎通テスト。

ナビゲーションに載っている画面がすべて 200 を返すことを確認する。未移植画面も
「未実装」表示の 200 を返す設計なので、ここで落ちたら URL 設定かテンプレートの不備。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.navigation import all_items
from apps.core.services.ai_settings import mask_secret


class HealthzTests(TestCase):
    def test_認証なしで200を返す(self):
        self.assertEqual(self.client.get(reverse("healthz")).status_code, 200)


class MaskSecretTests(TestCase):
    def test_未設定は未設定と表示する(self):
        self.assertEqual(mask_secret(""), "未設定")

    def test_先頭数文字だけ残す(self):
        self.assertEqual(mask_secret("sk-abcdefgh"), "sk-a*******")

    def test_短い値は全部伏せる(self):
        self.assertEqual(mask_secret("abc"), "***")


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
        response = self.client.get(reverse("dashboard:control"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_設定画面に生のAPIキーを出さない(self):
        with self.settings(OPENAI={**self._openai_settings(), "API_KEY": "sk-super-secret-value"}):
            response = self.client.get(reverse("core:settings"))

            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "sk-super-secret-value")
            self.assertContains(response, "sk-s")

    def _openai_settings(self) -> dict:
        from django.conf import settings

        return dict(settings.OPENAI)


class RoleVisibilityTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_参照のみのロールにも設定画面を出す(self):
        """AI設定は全ロールへ出す。

        API キーは費用と利用ログの単位が個人なので、利用者ごとに持てる必要がある。
        管理者だけが開ける画面のままだと、他のロールは自分のキーを入れる場所へ
        辿り着けない。テナント既定を編集できるかは画面内で分ける。
        """

        viewer = User.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.VIEWER,
        )
        self.client.force_login(viewer)
        response = self.client.get(reverse("dashboard:control"))

        self.assertContains(response, reverse("core:settings"))

    def test_テナント管理者には設定画面を出す(self):
        admin_user = User.objects.create_user(
            username="tenant-admin",
            email="tenant-admin@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(admin_user)
        response = self.client.get(reverse("dashboard:control"))

        self.assertContains(response, reverse("core:settings"))


class SettingsPermissionTests(TestCase):
    """設定画面の権限境界。

    閲覧と個人設定は全ロールへ開き、**テナント既定の書き換えだけ**を管理者に限る。
    宣言だけでビューが素通しだと、メニューに出ない操作へ POST を直接投げられて
    権限境界が形だけになるため、ビュー側でも同じ条件を強制する。
    """

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.admin = User.objects.create_user(
            username="admin-user",
            email="admin@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.member = User.objects.create_user(
            username="pmo-user",
            email="pmo@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.PMO,
        )

    def test_管理者は設定画面を開ける(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(reverse("core:settings"))
        self.assertEqual(response.status_code, 200)

    def test_管理者以外も自分の設定のために開ける(self) -> None:
        self.client.force_login(self.member)
        response = self.client.get(reverse("core:settings"))
        self.assertEqual(response.status_code, 200)

    def test_管理者以外はテナント既定を書き換えられない(self) -> None:
        from apps.core.models import TenantAISetting

        self.client.force_login(self.member)
        response = self.client.post(
            reverse("core:settings"),
            {"scope": "tenant", "is_active": "on", "provider": "ollama"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TenantAISetting.objects.filter(tenant=self.tenant).exists())

    def test_未ログインはログインへ送る(self) -> None:
        response = self.client.get(reverse("core:settings"))
        self.assertEqual(response.status_code, 302)
