"""連携画面のテナント分離と権限。

接続設定は取込元そのものなので、書き換えられると内部データを丸ごと汚染できる。
「他テナントのものは存在しない（404）」「一般ユーザーは変更できない（403）」を
経路ごとに確認する。
"""

from __future__ import annotations

from collections.abc import Iterable
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.integrations.models import Connection, Provider, SyncJob
from apps.integrations.services.connectors.base import (
    BaseConnector,
    ConnectionStatus,
    ExternalIssue,
)
from apps.projects.models import Issue, Project


class _FixedConnector(BaseConnector):
    provider = "stub"

    def check(self) -> ConnectionStatus:
        return ConnectionStatus(ok=True, message="疎通しました")

    def fetch_issues(self) -> Iterable[ExternalIssue]:
        return [ExternalIssue(external_id="PMO-1", key="PMO-1", title="取込テスト", status="Open")]


class IntegrationViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant_a = Tenant.objects.create(code="a", name="テナントA")
        self.tenant_b = Tenant.objects.create(code="b", name="テナントB")
        self.project_a = Project.objects.create(tenant=self.tenant_a, code="a1", name="A案件1")

        self.admin = User.objects.create_user(
            username="admin-a",
            email="admin-a@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.TENANT_ADMIN,
        )
        self.member = User.objects.create_user(
            username="member-a",
            email="member-a@example.com",
            password="x",
            tenant=self.tenant_a,
            role=Role.PMO,
        )

        self.connection_a = Connection.objects.create(
            tenant=self.tenant_a,
            project=self.project_a,
            provider=Provider.JIRA,
            name="A社Jira",
            mode=Connection.Mode.MOCK,
            config={"project_key": "PMO"},
        )
        self.connection_b = Connection.objects.create(
            tenant=self.tenant_b,
            provider=Provider.REDMINE,
            name="B社Redmine",
        )

    def _payload(self, **overrides) -> dict:
        payload = {
            "project": str(self.project_a.pk),
            "provider": Provider.JIRA,
            "name": "新しい接続",
            "base_url": "https://example.invalid",
            "credential_env": "JIRA_API_TOKEN",
            "mode": Connection.Mode.MOCK,
            "config": '{"project_key": "NEW"}',
            "is_active": "on",
        }
        payload.update(overrides)

        return payload

    # ── 参照 ────────────────────────────────────────────────

    def test_一覧は自テナントの接続だけを出す(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("integrations:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A社Jira")
        self.assertNotContains(response, "B社Redmine")

    def test_一覧はモックか実APIかを明示する(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("integrations:list"))

        self.assertContains(response, "モック")

    def test_同期履歴は自テナントの分だけ出す(self):
        SyncJob.objects.create(connection=self.connection_a, message="A社の同期")
        SyncJob.objects.create(connection=self.connection_b, message="B社の同期")
        self.client.force_login(self.member)

        response = self.client.get(reverse("integrations:job_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A社Jira")
        self.assertNotContains(response, "B社Redmine")

    # ── 権限 ────────────────────────────────────────────────

    def test_一般ユーザーは接続を追加できない(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("integrations:create"))

        self.assertEqual(response.status_code, 403)

    def test_一般ユーザーは接続を編集できない(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("integrations:edit", args=[self.connection_a.pk]))

        self.assertEqual(response.status_code, 403)

    def test_他テナントの接続は存在しない扱いになる(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("integrations:edit", args=[self.connection_b.pk]))

        self.assertEqual(response.status_code, 404)

    def test_他テナントの接続は同期できない(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("integrations:sync", args=[self.connection_b.pk]))

        self.assertEqual(response.status_code, 404)

    # ── 追加・編集 ──────────────────────────────────────────

    def test_テナント管理者は接続を追加できる(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("integrations:create"), self._payload())

        self.assertRedirects(response, reverse("integrations:list"))
        created = Connection.objects.get(name="新しい接続")
        self.assertEqual(created.tenant, self.tenant_a)
        self.assertEqual(created.config, {"project_key": "NEW"})

    def test_資格情報の値を貼り付けると保存できない(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("integrations:create"),
            self._payload(credential_env="ATATT-secret-token-value"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Connection.objects.filter(name="新しい接続").exists())
        # エラー文に入力値をそのまま載せない（載せると画面とログへ秘密が出る）。
        self.assertNotContains(response, "ATATT-secret-token-value")

    def test_実APIモードは資格情報の環境変数名を要求する(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("integrations:create"),
            self._payload(mode=Connection.Mode.LIVE, credential_env=""),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Connection.objects.filter(name="新しい接続").exists())

    # ── 実行 ────────────────────────────────────────────────

    def test_同期を実行すると結果が一覧へ返る(self):
        self.client.force_login(self.member)

        with patch(
            "apps.integrations.services.sync.get_connector",
            side_effect=lambda connection: _FixedConnector(connection),
        ):
            response = self.client.post(
                reverse("integrations:sync", args=[self.connection_a.pk]), follow=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Issue.objects.count(), 1)

        job = SyncJob.objects.get()
        self.assertEqual(job.status, SyncJob.Status.SUCCEEDED)
        self.assertEqual(job.triggered_by, self.member)
        self.assertContains(response, "新規 1")

    def test_同期はGETでは実行できない(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("integrations:sync", args=[self.connection_a.pk]))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(SyncJob.objects.count(), 0)

    def test_疎通確認は結果をメッセージで返す(self):
        self.client.force_login(self.member)

        with patch(
            "apps.integrations.services.connections.get_connector",
            side_effect=lambda connection: _FixedConnector(connection),
        ):
            response = self.client.post(
                reverse("integrations:check", args=[self.connection_a.pk]), follow=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "疎通しました")
