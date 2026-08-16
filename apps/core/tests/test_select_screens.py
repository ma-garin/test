"""テナント選択・案件選択の説明表示（UXP-36 / UXP-37）。

切替は「押してみて確かめる」操作にしてはいけない。切替後に何が見えるのか・
どこへ着地するのかを、確定する前に画面で読める状態を固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project


class SelectTenantScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme-sel", name="ACME")
        Project.objects.create(tenant=self.tenant, code="a1", name="案件アルファ")
        Project.objects.create(tenant=self.tenant, code="b1", name="案件ベータ")

        self.user = User.objects.create_user(
            username="pmo-select",
            email="pmo-select@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.client.force_login(self.user)

        self.url = reverse("accounts:select_tenant")

    def test_選択肢ごとに参照できる範囲と遷移先が出る(self):
        response = self.client.get(self.url)

        self.assertContains(response, "案件 2 件")
        self.assertContains(response, "利用者 1 名")
        self.assertContains(response, "コントロールタワー（案件の絞り込みなし）")

    def test_nextの戻り先と切替で変わることを確定前に読める(self):
        response = self.client.get(self.url, {"next": "/projects/issues/"})

        self.assertContains(response, "/projects/issues/")
        self.assertContains(response, "へは戻りません")
        self.assertContains(response, "対象案件の絞り込みは解除")

    def test_説明を足しても切替の遷移先は変えていない(self):
        response = self.client.post(self.url, {"tenant": str(self.tenant.pk)})

        self.assertRedirects(
            response, reverse("dashboard:control"), fetch_redirect_response=False
        )


class SelectProjectScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme-proj", name="ACME")
        self.alpha = Project.objects.create(
            tenant=self.tenant, code="a1", name="案件アルファ"
        )
        Project.objects.create(tenant=self.tenant, code="b1", name="案件ベータ")

        self.user = User.objects.create_user(
            username="pmo-proj",
            email="pmo-proj@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        self.url = reverse("accounts:select_project")

    def test_全案件と個別案件を件数と画面名で比較する(self):
        response = self.client.get(self.url)

        self.assertContains(response, "全案件と個別案件の違い")
        self.assertContains(response, "2 件すべて")
        self.assertContains(response, "影響する画面")
        self.assertContains(response, "件の合算")

    def test_比較表はいま参照できる件数を数えている(self):
        Project.objects.create(tenant=self.tenant, code="c1", name="案件ガンマ")

        response = self.client.get(self.url)

        self.assertContains(response, "3 件すべて")

    def test_個別案件を選ぶと比較表がその案件を指す(self):
        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})

        response = self.client.get(self.url)

        self.assertContains(response, "いまは個別案件")
        self.assertContains(response, "a1 案件アルファ")

    def test_自分自身を指すnextは戻り先にしない(self):
        # ヘッダーの案件チップを案件選択画面で押すと、自分自身が next に入る。
        # そのまま採用すると、押すたびに入れ子と多重エンコードが積み上がる。
        response = self.client.post(
            self.url,
            {
                "project": str(self.alpha.pk),
                "next": "/accounts/project/?next=/accounts/project/",
            },
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_案件選択画面のヘッダーはnextを積み増さない(self):
        response = self.client.get(self.url)

        self.assertNotContains(response, 'href="/accounts/project/?next=')
