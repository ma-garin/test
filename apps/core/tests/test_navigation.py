"""サイドバーの可視性。

メニューに出ているのに開くと 403、という状態はいちばん質が悪い。実装が
判断に使っているのと同じ関数（`permissions.can()`）で可視性を決めているか、
ロールごとに固定する。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.constants import Action, Role
from apps.accounts.models import Tenant, User
from apps.core.navigation import all_items, item_by_url_name, navigation_for


def visible_keys(user) -> set[str]:
    return {item.key for section in navigation_for(user) for item in section.items}


class NavigationVisibilityTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def _user(self, role: str) -> User:
        return User.objects.create_user(
            username=f"nav-{role}",
            email=f"nav-{role}@example.com",
            password="x",
            tenant=self.tenant,
            role=role,
        )

    def test_未認証には何も出さない(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(navigation_for(AnonymousUser()), [])
        self.assertEqual(navigation_for(None), [])

    def test_参照のみには管理系を出さない(self):
        keys = visible_keys(self._user(Role.VIEWER))

        for key in ("integrations", "pipeline", "sync_jobs", "operations", "feedback"):
            with self.subTest(item=key):
                self.assertNotIn(key, keys)

    def test_参照のみにも設定画面は出す(self):
        """AI の API 設定は利用者ごとに持つ。入口が無いと自分のキーを入れられない。"""

        self.assertIn("settings", visible_keys(self._user(Role.VIEWER)))

    def test_承認できるロールには監査を出す(self):
        keys = visible_keys(self._user(Role.PMO))

        self.assertIn("operations", keys)
        self.assertIn("feedback", keys)
        # 外部連携は管理権限が要る。PMO は manage を持たない。
        self.assertNotIn("integrations", keys)

    def test_テナント管理者には全部出す(self):
        keys = visible_keys(self._user(Role.TENANT_ADMIN))

        self.assertEqual(keys, {item.key for item in all_items()})

    def test_変更管理者には承認が要る画面を出さない(self):
        keys = visible_keys(self._user(Role.CHANGE_MANAGER))

        self.assertNotIn("operations", keys)
        self.assertIn("change", keys)


class NavigationConsistencyTests(TestCase):
    def test_項目のキーとURL名は重複しない(self):
        items = all_items()

        self.assertEqual(len({item.key for item in items}), len(items))
        self.assertEqual(len({item.url_name for item in items}), len(items))

    def test_URL名から項目を引ける(self):
        item = item_by_url_name("core:settings")

        self.assertIsNotNone(item)
        self.assertEqual(item.key, "settings")
        self.assertIsNone(item_by_url_name("存在しない:画面"))

    def test_必要な操作は定義済みのものだけ(self):
        for item in all_items():
            with self.subTest(item=item.key):
                self.assertIn(item.action, Action.values)

    def test_現在の画面を含むセクションが開く(self):
        tenant = Tenant.objects.create(code="beta", name="BETA")
        user = User.objects.create_user(
            username="nav-current",
            email="nav-current@example.com",
            password="x",
            tenant=tenant,
            role=Role.PMO,
        )
        sections = navigation_for(user, "core:settings")
        current = [section for section in sections if section.is_current]

        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].key, "admin")
