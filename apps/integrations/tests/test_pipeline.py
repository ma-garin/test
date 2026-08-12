"""同期の稼働状況の集計（traceability #35 / #36）。

この機能の存在理由は「同期が止まっていることに気づけない」を潰すことなので、
警告が出ること・出ないことの境界と、テナント分離を確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import IngestJob
from apps.integrations.models import Connection, Provider, SyncJob
from apps.integrations.services.pipeline import STALE_AFTER_HOURS, build_pipeline_overview


class PipelineOverviewTests(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()
        self.tenant = Tenant.objects.create(code="a", name="テナントA")
        self.other_tenant = Tenant.objects.create(code="b", name="テナントB")
        self.user = User.objects.create_user(
            username="member-a",
            email="member-a@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )

        self.fresh = self._connection("直近同期あり", last_synced_at=self.now)
        self.stale = self._connection(
            "止まっている接続", last_synced_at=self.now - timedelta(hours=72)
        )
        self.never = self._connection("一度も同期していない接続")
        self.disabled = self._connection("停止中の接続", is_active=False)

        self._job(self.fresh, finished_at=self.now - timedelta(minutes=5))
        self._job(self.stale, finished_at=self.now - timedelta(hours=STALE_AFTER_HOURS + 5))
        self._job(self.disabled, finished_at=self.now - timedelta(days=30))

    def _connection(self, name, *, last_synced_at=None, is_active=True, tenant=None):
        return Connection.objects.create(
            tenant=tenant or self.tenant,
            provider=Provider.JIRA,
            name=name,
            last_synced_at=last_synced_at,
            is_active=is_active,
        )

    def _job(self, connection, *, finished_at, status=SyncJob.Status.SUCCEEDED):
        return SyncJob.objects.create(
            connection=connection,
            status=status,
            started_at=finished_at,
            finished_at=finished_at,
            message="取込しました",
        )

    def _overview(self):
        return build_pipeline_overview(self.user, self.tenant, now=self.now)

    def test_stale_connection_is_flagged(self) -> None:
        names = {row.connection.name for row in self._overview().stale_rows}

        self.assertIn("止まっている接続", names)

    def test_never_synced_connection_is_flagged(self) -> None:
        """一度も成功していないのも異常として扱う。"""

        names = {row.connection.name for row in self._overview().stale_rows}

        self.assertIn("一度も同期していない接続", names)

    def test_fresh_connection_is_not_flagged(self) -> None:
        names = {row.connection.name for row in self._overview().stale_rows}

        self.assertNotIn("直近同期あり", names)

    def test_disabled_connection_is_not_flagged(self) -> None:
        """無効化した接続が止まっているのは意図どおりなので警告しない。"""

        names = {row.connection.name for row in self._overview().stale_rows}

        self.assertNotIn("停止中の接続", names)

    def test_failed_job_does_not_count_as_success(self) -> None:
        """失敗しか無い接続は「成功した同期が無い」として扱う。"""

        connection = self._connection("失敗のみの接続")
        self._job(connection, finished_at=self.now, status=SyncJob.Status.FAILED)

        row = next(r for r in self._overview().rows if r.connection.pk == connection.pk)

        self.assertIsNone(row.last_success_at)
        self.assertTrue(row.is_stale)

    def test_elapsed_hours_are_measured_from_last_success(self) -> None:
        row = next(r for r in self._overview().rows if r.connection.pk == self.stale.pk)

        self.assertAlmostEqual(row.hours_since_success, STALE_AFTER_HOURS + 5, places=1)
        self.assertEqual(row.last_synced_at, self.stale.last_synced_at)

    def test_other_tenant_connection_is_excluded(self) -> None:
        self._connection("別テナントの接続", tenant=self.other_tenant)

        names = {row.connection.name for row in self._overview().rows}

        self.assertNotIn("別テナントの接続", names)

    def test_index_job_is_reported(self) -> None:
        IngestJob.objects.create(
            tenant=self.tenant,
            job_type=IngestJob.JobType.INDEX,
            status=IngestJob.Status.SUCCEEDED,
            finished_at=self.now - timedelta(hours=1),
        )
        IngestJob.objects.create(
            tenant=self.other_tenant,
            job_type=IngestJob.JobType.INDEX,
            status=IngestJob.Status.SUCCEEDED,
            finished_at=self.now,
        )

        overview = self._overview()

        self.assertIsNotNone(overview.last_index_job)
        self.assertEqual(overview.last_index_job.tenant_id, self.tenant.pk)


class PipelineViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="a", name="テナントA")
        self.user = User.objects.create_user(
            username="member-a",
            email="member-a@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            provider=Provider.JIRA,
            name="止まっているJira",
            last_synced_at=timezone.now() - timedelta(days=5),
        )
        SyncJob.objects.create(
            connection=self.connection,
            status=SyncJob.Status.SUCCEEDED,
            started_at=timezone.now() - timedelta(days=5),
            finished_at=timezone.now() - timedelta(days=5),
        )

    def test_login_required(self) -> None:
        response = self.client.get(reverse("integrations:pipeline"))

        self.assertEqual(response.status_code, 302)

    def test_warning_is_shown_for_stale_connection(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("integrations:pipeline"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "callout d")
        self.assertContains(response, "止まっているJira")

    def test_empty_state_does_not_break(self) -> None:
        Connection.objects.all().delete()
        self.client.force_login(self.user)
        response = self.client.get(reverse("integrations:pipeline"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "callout d")
