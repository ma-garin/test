"""サイドメニューの常時表示と「その他」への畳み込み。

項目が 30 件近くあり、畳んだ状態では画面に収まらないという指摘への対応。
守りたいのは 1 点。**現在開いている画面は必ず常時表示側に出る。**
畳んだ中に現在地があると、自分がどこにいるか分からなくなる。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.navigation import NAVIGATION, navigation_for


class NavigationPriorityTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )

    def test_常時表示は全体の一部に絞られる(self) -> None:
        sections = navigation_for(self.user)
        total = sum(len(section.items) for section in sections)
        primary = sum(len(section.primary_items) for section in sections)

        self.assertLess(primary, total)
        self.assertGreater(primary, 0)

    def test_常時表示とその他で全項目を覆う(self) -> None:
        """畳み込みで到達できない画面を作らない。"""

        for section in navigation_for(self.user):
            covered = list(section.primary_items) + list(section.secondary_items)

            self.assertEqual(
                sorted(item.key for item in covered),
                sorted(item.key for item in section.items),
            )

    def test_現在開いている画面は常時表示へ引き上げる(self) -> None:
        sections = navigation_for(self.user, current_url_name="dashboard:kpi")
        control = next(section for section in sections if section.key == "control")

        self.assertIn("kpi", [item.key for item in control.primary_items])
        self.assertNotIn("kpi", [item.key for item in control.secondary_items])

    def test_既定ではKPIはその他に入る(self) -> None:
        sections = navigation_for(self.user)
        control = next(section for section in sections if section.key == "control")

        self.assertIn("kpi", [item.key for item in control.secondary_items])

    def test_全項目のURL名が一意(self) -> None:
        names = [item.url_name for section in NAVIGATION for item in section.items]

        self.assertEqual(len(names), len(set(names)))


class NavigationRenderTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["tenant_id"] = str(self.tenant.pk)
        session.save()

    def test_サイドバーにその他の畳み込みが出る(self) -> None:
        response = self.client.get(reverse("projects:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "その他")
        self.assertContains(response, "sb-more")

    def test_その他の中の画面も開ける(self) -> None:
        response = self.client.get(reverse("dashboard:kpi"))

        self.assertEqual(response.status_code, 200)
