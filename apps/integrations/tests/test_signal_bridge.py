"""LDF-05: Jira／Redmine の増分取込を Signal へ橋渡しする。

受入条件「課題・不具合が予測用 Signal として、時刻・URL つきで入る」。
外部へは一切書き込まないこと、増分であることを固定する。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.forecast.models import Signal, SignalClassification, SignalSource
from apps.integrations.models import Connection, NotificationLog, Provider
from apps.integrations.services import signal_bridge
from apps.integrations.services.connectors.base import ExternalIssue
from apps.integrations.services.signal_bridge import bridge_issues
from apps.projects.models import Project


class SignalBridgeTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.now = timezone.now()
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.JIRA,
            name="Jira（読み取り専用）",
            base_url="https://example.atlassian.net",
        )

    def _issue(self, key: str, *, hours_ago: int = 1, labels=(), title="不具合") -> ExternalIssue:
        return ExternalIssue(
            external_id=key,
            key=key,
            title=title,
            status="進行中",
            url=f"https://example.atlassian.net/browse/{key}",
            updated_at=self.now - timedelta(hours=hours_ago),
            labels=tuple(labels),
        )

    def test_issue_becomes_a_signal_with_time_and_url(self):
        result = bridge_issues(self.connection, [self._issue("PRJ-1")])
        self.assertEqual(result.created, 1)

        signal = Signal.objects.get()
        self.assertEqual(signal.source, SignalSource.JIRA)
        self.assertTrue(signal.permalink.endswith("/browse/PRJ-1"))
        self.assertIsNotNone(signal.occurred_at)

    def test_bug_label_is_classified_as_a_defect(self):
        bridge_issues(self.connection, [self._issue("PRJ-2", labels=("bug",))])
        self.assertEqual(
            Signal.objects.get().classification, SignalClassification.DEFECT_UPDATED
        )

    def test_unlabelled_issue_is_not_assumed_to_be_a_defect(self):
        bridge_issues(self.connection, [self._issue("PRJ-3")])
        self.assertEqual(
            Signal.objects.get().classification, SignalClassification.ISSUE_UPDATED
        )

    def test_only_updates_after_the_last_sync_are_taken(self):
        self.connection.last_synced_at = self.now - timedelta(hours=2)
        self.connection.save()

        result = bridge_issues(
            self.connection,
            [self._issue("OLD-1", hours_ago=5), self._issue("NEW-1", hours_ago=1)],
        )
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped_old, 1)
        self.assertEqual(Signal.objects.count(), 1)

    def test_resend_of_the_same_state_is_a_duplicate(self):
        issue = self._issue("PRJ-4")
        bridge_issues(self.connection, [issue])
        result = bridge_issues(self.connection, [issue])
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(Signal.objects.count(), 1)

    def test_changed_issue_creates_a_new_signal(self):
        bridge_issues(self.connection, [self._issue("PRJ-5", title="修正前")])
        result = bridge_issues(self.connection, [self._issue("PRJ-5", title="修正後")])
        self.assertEqual(result.created, 1)
        self.assertEqual(Signal.objects.count(), 2)

    def test_connection_without_project_is_skipped(self):
        connection = Connection.objects.create(
            tenant=self.tenant,
            provider=Provider.JIRA,
            name="案件未割当",
        )
        result = bridge_issues(connection, [self._issue("PRJ-6")])
        self.assertEqual(result.created, 0)
        self.assertEqual(Signal.objects.count(), 0)

    def test_unsupported_provider_is_skipped(self):
        connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.SLACK,
            name="Slack通知",
        )
        result = bridge_issues(connection, [self._issue("PRJ-7")])
        self.assertEqual(result.created, 0)

    def test_summary_line_reports_the_breakdown(self):
        self.connection.last_synced_at = self.now - timedelta(hours=2)
        self.connection.save()
        result = bridge_issues(
            self.connection,
            [self._issue("OLD-1", hours_ago=5), self._issue("NEW-1", hours_ago=1)],
        )
        self.assertIn("増分対象外 1件", result.summary_line())

    def test_bridge_does_not_write_to_the_external_system(self):
        """読み取り専用。橋渡しは内部レコードだけを作り、外部へ送らない。"""
        bridge_issues(self.connection, [self._issue("PRJ-8")])

        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.last_synced_at)
        self.assertEqual(NotificationLog.objects.count(), 0)
        self.assertEqual(Signal.objects.count(), 1)

    def test_module_has_no_outbound_http_client(self):
        """実装が HTTP クライアントを持ち込んでいないことを、輸入の有無で固定する。"""
        source = Path(signal_bridge.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import requests", source)
        self.assertNotIn("urlopen", source)
