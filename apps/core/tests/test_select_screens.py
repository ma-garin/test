"""案件選択画面の戻り先（next）。

ヘッダーの案件チップは戻り先として現在URLを `next` に載せる。案件選択画面
自身にも同じヘッダーが出るため、その画面で押すと自分自身が `next` に入り、
押すたびに入れ子と多重エンコードが積み上がっていた。リンク側とビュー側の
両方で止めていることを固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Project


class SelectProjectNextTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme-proj", name="ACME")
        self.alpha = Project.objects.create(
            tenant=self.tenant, code="a1", name="案件アルファ"
        )

        self.user = User.objects.create_user(
            username="pmo-proj",
            email="pmo-proj@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        self.url = reverse("accounts:select_project")

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

    def test_他画面を指すnextはそのまま戻り先になる(self):
        response = self.client.post(
            self.url,
            {"project": str(self.alpha.pk), "next": "/projects/issues/"},
        )

        self.assertRedirects(
            response, "/projects/issues/", fetch_redirect_response=False
        )
