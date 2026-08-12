"""VP-62: 一覧の絞り込みを、条件を指定せずに押したときの応答。

押したのに画面が変わらない状態は、0 件であることではなく「動いていない」と読まれる。
`request.GET.get(key, "")` は「キーが無い」と「キーが空」を区別しないため、
送信したこと自体を画面に伝える判定を `context_processors` に置いている。

ここで固定するのは次の 3 つ。

- 条件を指定せずに送信したら、押す前と異なる文言が出る
- 押す前（クエリなし）には出ない
- 条件を 1 つでも指定したときは出ない（正常な絞り込みを邪魔しない）
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User

#: 通知の目印。文言を変えても、要素が消えたことは検出できるようにする。
NOTICE_MARKER = "data-search-notice"

#: 絞り込みフォームを持つ一覧画面と、その画面が受け取る条件パラメータ。
#: 監査で「押しても無反応」と判定された 9 画面すべてを並べる。
LIST_SCREENS: tuple[tuple[str, str], ...] = (
    ("agents:run_list", "area"),
    ("dashboard:change", "status"),
    ("documents:list", "q"),
    ("integrations:job_list", "status"),
    ("audit:feedback_list", "kind"),
    ("projects:defect_list", "severity"),
    ("dashboard:intervention", "status"),
    ("projects:issue_list", "status"),
    ("dashboard:risk", "status"),
)


class FilterFeedbackTests(TestCase):
    """9 画面すべてで、押した反応が返ること。"""

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

    def test_条件未指定で絞り込むと押した反応が出る(self) -> None:
        for url_name, param in LIST_SCREENS:
            with self.subTest(screen=url_name):
                response = self.client.get(reverse(url_name), {param: ""})

                self.assertContains(response, NOTICE_MARKER)
                self.assertContains(response, "条件が未指定のため、絞り込んでいません。")

    def test_押す前の画面には通知を出さない(self) -> None:
        for url_name, _ in LIST_SCREENS:
            with self.subTest(screen=url_name):
                response = self.client.get(reverse(url_name))

                self.assertNotContains(response, NOTICE_MARKER)

    def test_ページ送りだけでは通知を出さない(self) -> None:
        """ページ送りは、利用者が条件を指定した操作ではない。"""

        for url_name, _ in LIST_SCREENS:
            with self.subTest(screen=url_name):
                response = self.client.get(reverse(url_name), {"page": "1"})

                self.assertNotContains(response, NOTICE_MARKER)


class FilterFeedbackContextTests(TestCase):
    """判定そのものの境界。画面を経由せずに固定する。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo2",
            email="pmo2@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_条件を1つでも指定したときは通知を出さない(self) -> None:
        response = self.client.get(reverse("projects:issue_list"), {"status": "open"})

        self.assertNotContains(response, NOTICE_MARKER)

    def test_空白だけの入力は未指定として扱う(self) -> None:
        response = self.client.get(reverse("documents:list"), {"q": "   "})

        self.assertContains(response, NOTICE_MARKER)
