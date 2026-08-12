"""検索・絞り込みを未入力で送信したときに、押した反応が返ること。

押しても画面が変わらない状態は、結果が無いことではなく「動いていない」と読まれる。
0 件であること自体は正常でも、**操作に対する応答は必ず返す**。

ここで固定するのは次の 2 つ。

- 未入力で送信したとき、送信前の画面と異なる文言が出る
- その文言が、次に何をすればよいかを述べている（原因だけを述べて終わらない）
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User

#: 通知の目印。文言を変えても、要素が消えたことは検出できるようにする。
NOTICE_MARKER = "data-search-notice"


class EmptySubmitBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)


class RagSearchEmptySubmitTests(EmptySubmitBase):
    """RAG 検索を空欄のまま押したとき。"""

    def test_未入力で検索すると押した反応が出る(self) -> None:
        response = self.client.get(reverse("rag:search"), {"q": ""})

        self.assertContains(response, NOTICE_MARKER)
        self.assertContains(response, "検索語が未入力のため、検索していません。")

    def test_押す前の画面には通知を出さない(self) -> None:
        response = self.client.get(reverse("rag:search"))

        self.assertNotContains(response, NOTICE_MARKER)

    def test_入力欄がブラウザ側でも未入力送信を防ぐ(self) -> None:
        response = self.client.get(reverse("rag:search"))

        self.assertContains(response, 'name="q"')
        self.assertContains(response, "required")


class ConsultationEmptySubmitTests(EmptySubmitBase):
    """PMO 相談を空欄のまま押したとき。"""

    def test_未入力で整理すると押した反応が出る(self) -> None:
        response = self.client.get(reverse("pmo:consultation"), {"q": ""})

        self.assertContains(response, NOTICE_MARKER)
        self.assertContains(response, "相談内容が未入力のため、整理していません。")

    def test_押す前の画面には通知を出さない(self) -> None:
        response = self.client.get(reverse("pmo:consultation"))

        self.assertNotContains(response, NOTICE_MARKER)


class OperationFilterEmptySubmitTests(EmptySubmitBase):
    """操作ログを条件未指定のまま絞り込んだとき。"""

    def test_条件未指定で絞り込むと押した反応が出る(self) -> None:
        response = self.client.get(reverse("audit:operation_list"), {"target": ""})

        self.assertContains(response, NOTICE_MARKER)
        self.assertContains(response, "条件が未指定のため、絞り込んでいません。")

    def test_押す前の画面には通知を出さない(self) -> None:
        response = self.client.get(reverse("audit:operation_list"))

        self.assertNotContains(response, NOTICE_MARKER)

    def test_条件を指定したときは通知を出さない(self) -> None:
        response = self.client.get(reverse("audit:operation_list"), {"period": "7"})

        self.assertNotContains(response, NOTICE_MARKER)
