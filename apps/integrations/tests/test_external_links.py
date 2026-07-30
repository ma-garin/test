"""内部レコードから外部原文へ辿れること（traceability #25）。

根拠トレースは最後に一次情報へ着地できないと意味がない。ここでは
「対応があるときだけリンクが出る」「他テナントの対応は混ざらない」
「対応が無くても画面が壊れない」を確認する。
"""

from __future__ import annotations

from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.integrations.models import Connection, Provider, SyncedRecord
from apps.projects.models import Defect, Issue, Project

LINK_TEMPLATE = "{% load integration_links %}{% external_link obj %}"
JIRA_URL = "https://jira.example.com/browse/PROJ-123"


def render_link(obj) -> str:
    return Template(LINK_TEMPLATE).render(Context({"obj": obj})).strip()


class ExternalLinkTagTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="a", name="テナントA")
        self.other_tenant = Tenant.objects.create(code="b", name="テナントB")
        self.project = Project.objects.create(tenant=self.tenant, code="a1", name="A案件1")
        self.issue = Issue.objects.create(project=self.project, title="取込課題")
        self.connection = Connection.objects.create(
            tenant=self.tenant, provider=Provider.JIRA, name="Jira本番"
        )
        self.other_connection = Connection.objects.create(
            tenant=self.other_tenant, provider=Provider.JIRA, name="別テナントJira"
        )
        self.user = User.objects.create_user(
            username="member-a",
            email="member-a@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )

    def _record(self, connection, obj, url=JIRA_URL, key="PROJ-123", entity_type=None):
        return SyncedRecord.objects.create(
            connection=connection,
            external_id=key,
            external_key=key,
            external_url=url,
            entity_type=entity_type or SyncedRecord.EntityType.ISSUE,
            object_id=obj.pk,
            last_synced_at=timezone.now(),
        )

    def test_no_record_renders_nothing(self) -> None:
        """対応が無ければ何も描かない（テンプレートは壊れない）。"""

        self.assertEqual(render_link(self.issue), "")

    def test_record_renders_external_link(self) -> None:
        self._record(self.connection, self.issue)
        html = render_link(self.issue)

        self.assertIn(JIRA_URL, html)
        self.assertIn("PROJ-123", html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_record_without_url_renders_nothing(self) -> None:
        """URL を持たない対応ではリンクにできない。"""

        self._record(self.connection, self.issue, url="")

        self.assertEqual(render_link(self.issue), "")

    def test_unsafe_scheme_is_rejected(self) -> None:
        """外部から取り込んだ URL を無検査で href に置かない。"""

        self._record(self.connection, self.issue, url="javascript:alert(1)")

        self.assertEqual(render_link(self.issue), "")

    def test_other_tenant_record_is_ignored(self) -> None:
        """同じ内部 ID を別テナントの接続が持っていても混ざらない。"""

        self._record(self.other_connection, self.issue)

        self.assertEqual(render_link(self.issue), "")

    def test_defect_record_renders_link(self) -> None:
        defect = Defect.objects.create(project=self.project, title="不具合1")
        self._record(
            self.connection,
            defect,
            key="BUG-9",
            entity_type=SyncedRecord.EntityType.DEFECT,
        )

        self.assertIn("BUG-9", render_link(defect))

    def test_unsupported_object_renders_nothing(self) -> None:
        """対応表に無いモデルを渡しても例外にしない。"""

        self.assertEqual(render_link(self.project), "")
        self.assertEqual(render_link(None), "")

    def test_issue_list_shows_external_link(self) -> None:
        self._record(self.connection, self.issue)
        self.client.force_login(self.user)
        response = self.client.get(reverse("projects:issue_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, JIRA_URL)
        self.assertContains(response, "noopener noreferrer")

    def test_issue_list_renders_without_record(self) -> None:
        """対応が無い状態でも一覧は 200 で描ける。"""

        self.client.force_login(self.user)
        response = self.client.get(reverse("projects:issue_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, JIRA_URL)
