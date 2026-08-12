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
    """ナビゲーションに載っている画面の (キー, URL, 必要ロール) 一覧。"""

    return [(item.key, reverse(item.url_name), item.roles) for item in all_items()]


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
        """ロール限定の画面は 403。それ以外は 200。どちらにせよ 500 にしない。

        ナビが「管理者だけ」と宣言している画面は、ビューも同じ条件を強制する。
        ここで 200 を期待すると、宣言と実際のアクセス可否がずれても気づけない。
        """

        self.assertIsNone(self.user.tenant)

        for key, url, roles in _screen_urls():
            with self.subTest(screen=key):
                response = self.client.get(url)
                expected = 403 if roles and self.user.role not in roles else 200

                self.assertEqual(response.status_code, expected)

    def test_テナント未選択なら監査データを何も見せない(self):
        response = self.client.get(reverse("audit:feedback_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"].total, 0)


class CrossTenantIsolationTests(TestCase):
    """他テナントのデータが画面へ混ざらないこと。"""

    #: 混入したら一目で分かるよう、他テナント側だけに現れる語を使う。
    OTHER_PROJECT_NAME = "GLOBEX機密統合案件"
    OWN_PROJECT_NAME = "ACME基幹刷新案件"

    def setUp(self) -> None:
        from apps.projects.models import Project

        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="Globex")
        self.user = User.objects.create_user(
            username="acme-admin",
            email="acme-admin@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        Project.objects.create(tenant=self.tenant, code="acme-core", name=self.OWN_PROJECT_NAME)
        Project.objects.create(tenant=self.other_tenant, code="gx-secret", name=self.OTHER_PROJECT_NAME)
        self.client.force_login(self.user)

    def test_全画面に他テナントの案件名が出ない(self):
        for key, url, roles in _screen_urls():
            with self.subTest(screen=key):
                response = self.client.get(url)

                if roles and self.user.role not in roles:
                    # 開けない画面は、そもそも中身を出さない。
                    self.assertEqual(response.status_code, 403)
                    continue

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.OTHER_PROJECT_NAME)

    def test_自テナントの案件は見える(self):
        response = self.client.get(reverse("projects:list"))

        self.assertContains(response, self.OWN_PROJECT_NAME)

    def test_他テナントの案件詳細は参照できない(self):
        from apps.projects.models import Project

        foreign = Project.objects.get(code="gx-secret")
        response = self.client.get(reverse("projects:detail", args=[foreign.pk]))

        self.assertIn(response.status_code, (403, 404))
