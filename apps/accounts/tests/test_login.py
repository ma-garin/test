"""メールアドレスのみのログイン。

パスワードを検証しない構成なので、「誰が入れるか」を決めているのは
`EmailOnlyBackend` だけになる。ここが唯一の防波堤なので直接検証する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User


class EmailLoginTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.url = reverse("accounts:login")

    def test_登録済みの利用者はパスワードなしで入れる(self):
        User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            tenant=self.tenant,
            role=Role.PMO,
        )

        response = self.client.post(self.url, {"email": "pmo@example.com"})

        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_大文字小文字が違っても同じ利用者になる(self):
        user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            tenant=self.tenant,
            role=Role.PMO,
        )

        self.client.post(self.url, {"email": "PMO@Example.com"})

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(user.pk))

    def test_未登録のメールアドレスは利用者を作って入れる(self):
        response = self.client.post(self.url, {"email": "newcomer@example.com"})

        self.assertEqual(response.status_code, 302)

        created = User.objects.get(email="newcomer@example.com")
        self.assertEqual(created.username, "newcomer")
        self.assertEqual(created.tenant, self.tenant)
        self.assertFalse(created.has_usable_password())

    def test_ユーザー名が衝突したら連番を足す(self):
        User.objects.create_user(
            username="taro",
            email="taro@example.com",
            tenant=self.tenant,
        )

        self.client.post(self.url, {"email": "taro@other.example.com"})

        self.assertEqual(User.objects.get(email="taro@other.example.com").username, "taro-2")

    def test_メールアドレスの形式が不正なら入れない(self):
        response = self.client.post(self.url, {"email": "not-an-email"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_無効化した利用者は入れない(self):
        User.objects.create_user(
            username="retired",
            email="retired@example.com",
            tenant=self.tenant,
            is_active=False,
        )

        response = self.client.post(self.url, {"email": "retired@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ログインできません")
