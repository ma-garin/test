"""管理者ロールの付与コマンド。

開発DBを直接書き換えていた操作の置き換えなので、
**再現できること（同じ入力で同じ結果）と、失敗を黙って飲み込まないこと**を検証する。
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User


class PromoteAdminCommandTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            tenant=self.tenant,
            role=Role.PMO,
        )

    def _run(self, *args: str) -> str:
        out = StringIO()
        call_command("promote_admin", *args, stdout=out)
        return out.getvalue()

    def test_テナント管理者へ昇格できる(self) -> None:
        output = self._run("--email", "pmo@example.com")

        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.TENANT_ADMIN)
        self.assertIn("pmo -> tenant_admin", output)

    def test_ロールを指定してシステム管理者にできる(self) -> None:
        self._run("--email", "pmo@example.com", "--role", "system_admin")

        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.SYSTEM_ADMIN)

    def test_存在しないメールはエラーで終わる(self) -> None:
        with self.assertRaises(CommandError) as raised:
            self._run("--email", "nobody@example.com")

        self.assertIn("nobody@example.com", str(raised.exception))

    def test_二度実行しても結果が変わらない(self) -> None:
        self._run("--email", "pmo@example.com")
        output = self._run("--email", "pmo@example.com")

        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.TENANT_ADMIN)
        self.assertIn("変更なし", output)

    def test_revokeでPMOへ戻せる(self) -> None:
        self._run("--email", "pmo@example.com")

        output = self._run("--email", "pmo@example.com", "--revoke")

        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.PMO)
        self.assertIn("tenant_admin -> pmo", output)

    def test_大文字小文字が違っても同じ利用者を指せる(self) -> None:
        self._run("--email", "PMO@Example.com")

        self.user.refresh_from_db()
        self.assertEqual(self.user.role, Role.TENANT_ADMIN)

    def test_秘密情報を出力しない(self) -> None:
        output = self._run("--email", "pmo@example.com")

        self.assertNotIn("password", output.lower())
        self.assertNotIn(self.user.password, output)
