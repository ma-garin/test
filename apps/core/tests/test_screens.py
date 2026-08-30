"""画面横断の防御テスト。

`test_views.py` の疎通テスト（所属テナントのある管理者で全画面 200）とは狙いが違う。
ここでは「壊れやすい経路」を 2 つ押さえる。

1. テナント未選択（`request.tenant` が None）の利用者でも全画面が落ちないこと
   … 各ビューが `request.tenant` を無条件に使うと例外になる。新規参画者や
   テナント未割当のアカウントで必ず通る経路なので、疎通テストより優先度が高い。
2. 他テナントのデータが画面に混ざらないこと
   … テナント分離は selectors に集約する設計だが、実際に混ざらないかは
   画面まで通して確かめないと保証できない。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.navigation import all_items


def _screen_urls() -> list[tuple[str, str]]:
    """ナビゲーションに載っている画面の (キー, URL) 一覧。"""

    return [(item.key, reverse(item.url_name)) for item in all_items()]


class TenantlessUserScreenTests(TestCase):
    """テナント未選択の利用者でも全画面が 200 を返すこと。"""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="no-tenant-user",
            email="no-tenant-user@example.com",
            password="test-password",
            tenant=None,
            role=Role.VIEWER,
        )
        self.client.force_login(self.user)

    def test_テナント未選択でも全画面が壊れない(self):
        self.assertIsNone(self.user.tenant)

        for key, url in _screen_urls():
            with self.subTest(screen=key):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)


class CrossTenantIsolationTests(TestCase):
    """他テナントのデータが画面へ混ざらないこと。"""

    #: 混入したら一目で分かるよう、他テナント側だけに現れる語を使う。
    OTHER_ORG_NAME = "Globex機密事業部"
    OWN_ORG_NAME = "ACME基幹事業部"

    def setUp(self) -> None:
        from apps.performance.constants import OrgLevel
        from apps.performance.models import OrgUnit

        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="Globex")
        self.user = User.objects.create_user(
            username="acme-admin",
            email="acme-admin@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        OrgUnit.objects.create(
            tenant=self.tenant, code="acme-div", name=self.OWN_ORG_NAME, level=OrgLevel.DIVISION
        )
        OrgUnit.objects.create(
            tenant=self.other_tenant, code="gx-div", name=self.OTHER_ORG_NAME, level=OrgLevel.DIVISION
        )
        self.client.force_login(self.user)

    def test_全画面に他テナントの組織名が出ない(self):
        for key, url in _screen_urls():
            with self.subTest(screen=key):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.OTHER_ORG_NAME)

    def test_自テナントの組織は見える(self):
        response = self.client.get(reverse("performance:org_list"))

        self.assertContains(response, self.OWN_ORG_NAME)
