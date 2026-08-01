"""サイドバーの項目表示。

以前は使用頻度の低い画面を「その他」へ畳んでいたが、利用者が自分の使う画面を
探せなくなるため廃止した。ここでは**全項目が並ぶこと**を固定する。

畳んだ状態（アイコン表示）で画面に収まるかは高さの問題であり、
項目を隠すことで解決しない。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.navigation import NAVIGATION, all_items, navigation_for


class NavigationItemsTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="nav", name="NAV")
        self.admin = User.objects.create_user(
            username="nav-admin",
            email="nav-admin@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )

    def test_全項目が返る(self):
        """権限で隠れるもの以外は、1 つも畳まれずに返ること。"""

        sections = navigation_for(self.admin)
        returned = sum(len(section.items) for section in sections)

        self.assertEqual(returned, len(all_items()))

    def test_定義した項目が欠けない(self):
        """セクションごとの件数が定義と一致すること。"""

        by_key = {section.key: section for section in navigation_for(self.admin)}

        for defined in NAVIGATION:
            with self.subTest(section=defined.key):
                self.assertEqual(len(by_key[defined.key].items), len(defined.items))

    def test_権限の無い項目だけは除かれる(self):
        viewer = User.objects.create_user(
            username="nav-viewer",
            email="nav-viewer@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.VIEWER,
        )

        visible = {item.key for s in navigation_for(viewer) for item in s.items}

        self.assertNotIn("settings", visible)
        self.assertIn("control_dashboard", visible)

    def test_項目の重複が無い(self):
        keys = [item.key for item in all_items()]

        self.assertEqual(len(keys), len(set(keys)))


class NavigationRenderTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="nav2", name="NAV2")
        self.user = User.objects.create_user(
            username="nav-render",
            email="nav-render@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_サイドバーに畳み込みが出ない(self):
        """「その他」は廃止した。復活していないことを固定する。"""

        response = self.client.get(reverse("dashboard:control"))

        self.assertNotContains(response, "その他")
        self.assertNotContains(response, "sb-more")

    def test_使用頻度の低い画面もサイドバーに出る(self):
        """以前「その他」へ畳んでいた画面が、直接見えること。"""

        response = self.client.get(reverse("dashboard:control"))

        for label in ("KPI・効果測定", "PoC合否判定", "操作ログ", "同期履歴", "ひな型管理"):
            with self.subTest(label=label):
                self.assertContains(response, label)
