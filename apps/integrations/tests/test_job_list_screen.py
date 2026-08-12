"""同期履歴画面の絞り込みと失敗行の導線（UXP-34）。

履歴は件数だけでは読めない。「どの条件で何件に絞られているか」と「失敗をどこで
直すか」が画面に無いと、データが無いのか絞り込んだ結果なのか区別できない。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.integrations.models import Connection, Provider, SyncJob
from apps.projects.models import Project


class JobListScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme-jobs", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="j1", name="案件J")
        self.user = User.objects.create_user(
            username="admin-jobs",
            email="admin-jobs@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        self.jira = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.JIRA,
            name="A社Jira",
            mode=Connection.Mode.MOCK,
        )
        self.redmine = Connection.objects.create(
            tenant=self.tenant,
            provider=Provider.REDMINE,
            name="A社Redmine",
            mode=Connection.Mode.MOCK,
        )

        SyncJob.objects.create(
            connection=self.jira,
            status=SyncJob.Status.SUCCEEDED,
            message="取込に成功しました",
        )
        SyncJob.objects.create(
            connection=self.redmine,
            status=SyncJob.Status.FAILED,
            message="取込に失敗しました",
        )
        old = SyncJob.objects.create(
            connection=self.jira,
            status=SyncJob.Status.SUCCEEDED,
            message="ずっと前の取込",
        )
        SyncJob.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=45)
        )

        self.url = reverse("integrations:job_list")

    def test_成否で絞り込める(self):
        response = self.client.get(self.url, {"result": "ng"})

        self.assertContains(response, "取込に失敗しました")
        self.assertNotContains(response, "取込に成功しました")

    def test_接続と期間で絞ると件数と適用条件が出る(self):
        response = self.client.get(
            self.url, {"connection": str(self.jira.pk), "period": "30"}
        )

        self.assertContains(response, "該当 1 件")
        self.assertContains(response, "接続: A社Jira")
        self.assertContains(response, "期間: 直近 30 日")
        self.assertContains(response, "条件をクリア")
        self.assertNotContains(response, "ずっと前の取込")

    def test_条件なしなら全件のままでクリア導線を出さない(self):
        response = self.client.get(self.url)

        self.assertContains(response, "該当 3 件")
        self.assertContains(response, "なし（すべての履歴）")
        self.assertNotContains(response, "条件をクリア")

    def test_失敗行から接続設定へ辿れる(self):
        failed = self.client.get(self.url, {"result": "ng"})

        self.assertContains(failed, reverse("integrations:edit", args=[self.redmine.pk]))

        succeeded = self.client.get(self.url, {"result": "ok"})

        # 成功行に「設定を見直す」導線は出さない。直すものが無いため。
        self.assertNotContains(
            succeeded, reverse("integrations:edit", args=[self.jira.pk])
        )

    def test_参照できない接続IDは条件として採用しない(self):
        other = Tenant.objects.create(code="beta-jobs", name="BETA")
        foreign = Connection.objects.create(
            tenant=other,
            provider=Provider.JIRA,
            name="他社Jira",
            mode=Connection.Mode.MOCK,
        )

        response = self.client.get(self.url, {"connection": str(foreign.pk)})

        self.assertNotContains(response, "他社Jira")
        self.assertContains(response, "取込に失敗しました")
