"""文書台帳とひな型一覧のページングテスト。

どちらも打ち切り（先頭 N 件）からページングへ移した箇所。件数が増えたときに
「見えていない行がある」ことに気づけるか、集計値がページ間でぶれないかを見る。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.pagination import PAGE_SIZE
from apps.documents.models import Document, Template
from apps.documents.views import CARDS_PER_PAGE


class DocumentListPaginationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-docs",
            email="pmo-docs@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        Document.objects.bulk_create(
            Document(
                tenant=self.tenant,
                title=f"設計書 {index:03d}",
                file=f"documents/doc-{index:03d}.pdf",
            )
            for index in range(PAGE_SIZE + 10)
        )

        self.url = reverse("documents:list")

    def test_1ページ目は既定件数までしか出さない(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page"].object_list), PAGE_SIZE)

    def test_2ページ目に残りが出る(self):
        response = self.client.get(self.url, {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page"].object_list), 10)

    def test_総件数は全件を示す(self):
        """ページで切っても「全部で何件あるか」は変わらないこと。"""

        first = self.client.get(self.url).context["page"].paginator.count
        second = self.client.get(self.url, {"page": 2}).context["page"].paginator.count

        self.assertEqual(first, PAGE_SIZE + 10)
        self.assertEqual(first, second)

    def test_範囲外のページ番号でも落ちない(self):
        """URL を手で編集した程度で 500 や 404 にしない。"""

        for value in ("0", "999", "abc", ""):
            with self.subTest(page=value):
                self.assertEqual(self.client.get(self.url, {"page": value}).status_code, 200)


class TemplateListPaginationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-tpl",
            email="pmo-tpl@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        Template.objects.bulk_create(
            Template(
                tenant=self.tenant,
                name=f"ひな型 {index:03d}",
                file=f"templates/tpl-{index:03d}.xlsx",
                field_mapping={"進捗率": "B4"},
            )
            for index in range(CARDS_PER_PAGE + 5)
        )

        self.url = reverse("documents:template_list")

    def test_カードは既定より少ない件数で区切る(self):
        """1 件がカードなので、一覧の既定件数だと縦に伸びすぎる。"""

        response = self.client.get(self.url)

        self.assertLess(CARDS_PER_PAGE, PAGE_SIZE)
        self.assertEqual(len(response.context["cards"]), CARDS_PER_PAGE)

    def test_2ページ目に残りが出る(self):
        response = self.client.get(self.url, {"page": 2})

        self.assertEqual(len(response.context["cards"]), 5)

    def test_集計はページを送っても変わらない(self):
        """マッピング済み件数はページ内ではなく全件から数える。"""

        first = self.client.get(self.url).context
        second = self.client.get(self.url, {"page": 2}).context

        self.assertEqual(first["template_total"], CARDS_PER_PAGE + 5)
        self.assertEqual(first["template_total"], second["template_total"])
        self.assertEqual(first["mapped_total"], second["mapped_total"])
