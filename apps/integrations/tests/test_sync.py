"""取込サービスの検証。

ここで守りたいのは「二重入力をなくす」ための 3 点。

1. モックのまま端から端まで通ること（API キー無しで検証できる）
2. 何度流しても増えないこと（冪等性）
3. 落とした値・失敗した明細が残ること（黙って消えない）
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.integrations.models import Connection, Provider, SyncedRecord, SyncJob
from apps.integrations.services.connectors.base import (
    BaseConnector,
    ConnectionStatus,
    ConnectorError,
    ExternalIssue,
)
from apps.integrations.services.sync import run_pull
from apps.projects.models import Issue, Project, Severity


class StubConnector(BaseConnector):
    """テスト用のコネクタ。外部へは一切出ない。"""

    provider = "stub"

    def __init__(self, connection, issues: Iterable[ExternalIssue]) -> None:
        super().__init__(connection)
        self._issues = list(issues)

    def check(self) -> ConnectionStatus:
        return ConnectionStatus(ok=True, message="stub")

    def fetch_issues(self) -> Iterable[ExternalIssue]:
        return list(self._issues)


def _issue(external_id: str = "PMO-1", **overrides) -> ExternalIssue:
    payload = {
        "external_id": external_id,
        "key": external_id,
        "title": "受入環境の払い出しが遅延",
        "description": "検証環境が未整備。",
        "status": "In Progress",
        "priority": "High",
        "assignee": "PMO",
        "due_date": date(2026, 9, 30),
        "url": "https://example.invalid/browse/PMO-1",
    }
    payload.update(overrides)

    return ExternalIssue(**payload)


def _stub(issues: Iterable[ExternalIssue]):
    """`get_connector` を差し替える。実 API どころかモックにも触らない。"""

    return patch(
        "apps.integrations.services.sync.get_connector",
        side_effect=lambda connection: StubConnector(connection, issues),
    )


class RunPullTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="t1", name="テナント1")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.JIRA,
            name="Jira（モック）",
            mode=Connection.Mode.MOCK,
            config={"project_key": "PMO"},
        )

    def test_モックのまま同期すると課題が取り込まれる(self):
        """API キー無しで、取込経路が端から端まで通ること。"""

        job = run_pull(self.connection)

        self.assertEqual(job.status, SyncJob.Status.SUCCEEDED)
        self.assertGreater(job.created_count, 0)
        self.assertEqual(Issue.objects.count(), job.created_count)
        self.assertEqual(SyncedRecord.objects.count(), job.created_count)
        self.assertTrue(all(issue.external_key for issue in Issue.objects.all()))

        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.last_synced_at)

    def test_2回流しても件数が増えず変更なしになる(self):
        issues = [_issue("PMO-1"), _issue("PMO-2", title="外部IF仕様の確定待ち")]

        with _stub(issues):
            first = run_pull(self.connection)
            second = run_pull(self.connection)

        self.assertEqual(first.created_count, 2)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.updated_count, 0)
        self.assertEqual(second.skipped_count, 2)
        self.assertEqual(Issue.objects.count(), 2)
        self.assertEqual(SyncedRecord.objects.count(), 2)

    def test_内容が変わった課題だけ更新される(self):
        with _stub([_issue("PMO-1")]):
            run_pull(self.connection)

        with _stub([_issue("PMO-1", title="受入環境の払い出しが遅延（再掲）")]):
            job = run_pull(self.connection)

        self.assertEqual(job.updated_count, 1)
        self.assertEqual(job.created_count, 0)
        self.assertEqual(Issue.objects.count(), 1)
        self.assertEqual(Issue.objects.first().title, "受入環境の払い出しが遅延（再掲）")

    def test_対応表にない状態は既定値へ落ちて履歴に残る(self):
        with _stub([_issue("PMO-9", status="Awaiting Triage", priority="Sev-9")]):
            job = run_pull(self.connection)

        issue = Issue.objects.get()
        self.assertEqual(issue.status, Issue.Status.OPEN)
        self.assertEqual(issue.severity, Severity.MEDIUM)

        unmapped = job.detail["unmapped"]
        self.assertEqual(len(unmapped), 2)
        self.assertEqual({row["field"] for row in unmapped}, {"status", "priority"})
        self.assertIn("Awaiting Triage", [row["raw"] for row in unmapped])

    def test_1件失敗しても残りは取り込まれる(self):
        broken = _issue("PMO-BAD", due_date="日付ではない文字列")

        with _stub([_issue("PMO-1"), broken, _issue("PMO-2", title="別の課題")]):
            job = run_pull(self.connection)

        self.assertEqual(job.status, SyncJob.Status.PARTIAL)
        self.assertEqual(job.created_count, 2)
        self.assertEqual(job.failed_count, 1)
        self.assertEqual(Issue.objects.count(), 2)
        self.assertEqual(job.detail["failures"][0]["external_id"], "PMO-BAD")

    def test_案件未設定の接続は明確に失敗する(self):
        self.connection.project = None
        self.connection.save(update_fields=["project"])

        job = run_pull(self.connection)

        self.assertEqual(job.status, SyncJob.Status.FAILED)
        self.assertIn("案件", job.message)
        self.assertEqual(Issue.objects.count(), 0)

        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.last_synced_at)

    def test_通知専用のプロバイダは取込できない(self):
        connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.SLACK,
            name="Slack（モック）",
        )

        job = run_pull(connection)

        self.assertEqual(job.status, SyncJob.Status.FAILED)
        self.assertEqual(Issue.objects.count(), 0)

    def test_無効な接続は同期しない(self):
        self.connection.is_active = False
        self.connection.save(update_fields=["is_active"])

        job = run_pull(self.connection)

        self.assertEqual(job.status, SyncJob.Status.FAILED)

    def test_内部の課題が消えていれば作り直す(self):
        with _stub([_issue("PMO-1")]):
            run_pull(self.connection)

        Issue.objects.all().delete()

        with _stub([_issue("PMO-1")]):
            job = run_pull(self.connection)

        # 対応表だけ残って「変更なし」を返し続けると、課題が永久に復活しない。
        self.assertEqual(job.created_count, 1)
        self.assertEqual(Issue.objects.count(), 1)
        self.assertEqual(SyncedRecord.objects.count(), 1)


class ConnectorResolutionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="t2", name="テナント2")

    def test_実APIモードで実装が無ければ明確に失敗する(self):
        connection = Connection.objects.create(
            tenant=self.tenant,
            provider="unknown",
            name="未知の連携先",
            mode=Connection.Mode.LIVE,
        )

        from apps.integrations.services.connectors import get_connector

        with self.assertRaises(ConnectorError):
            get_connector(connection)
