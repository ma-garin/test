"""LDF-06: Slack の許可チャンネル読み取りと、関連候補。

受入条件「会話から候補を拾えるが、確認前は予測の確定根拠に使われない」。
DM・非許可チャンネル・全文保存を既定で行わないことも固定する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.forecast.models import Signal, SignalClassification
from apps.forecast.models.signals import MAX_EXCERPT_LENGTH
from apps.forecast.services.engine import compute_project_forecast
from apps.graph.models import Feature, WorkingCalendar
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance
from apps.integrations.models import Connection, Provider
from apps.integrations.services.slack_ingest import (
    MAX_SLACK_EXCERPT,
    SlackMessage,
    allowed_channels,
    ingest_messages,
)
from apps.projects.models import Issue, Project, Severity, WbsTask


class SlackIngestTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        WorkingCalendar.objects.create(project=self.project)
        self.feature = Feature.objects.create(project=self.project, name="受注登録")
        self.now = timezone.now()
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.SLACK,
            name="Slack（読み取り専用）",
            config={"channels": ["C-ATLAS-QA"]},
        )

    def _message(self, text: str, *, channel="C-ATLAS-QA", ts="1", **overrides) -> SlackMessage:
        defaults = {
            "channel": channel,
            "ts": ts,
            "text": text,
            "author": "山田",
            "permalink": f"https://example.invalid/archives/{channel}/p{ts}",
            "occurred_at": self.now,
        }
        defaults.update(overrides)
        return SlackMessage(**defaults)

    # ── 許可範囲 ─────────────────────────────────────────────
    def test_unconfigured_connection_allows_nothing(self):
        connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.SLACK,
            name="未設定",
        )
        self.assertEqual(allowed_channels(connection), frozenset())

    def test_allowed_channel_is_ingested(self):
        result = ingest_messages(self.connection, [self._message("再現しました")])
        self.assertEqual(result.created, 1)
        self.assertEqual(Signal.objects.count(), 1)

    def test_unlisted_channel_is_rejected(self):
        result = ingest_messages(
            self.connection, [self._message("雑談", channel="C-RANDOM")]
        )
        self.assertEqual(result.rejected_channel, 1)
        self.assertEqual(Signal.objects.count(), 0)

    def test_direct_message_is_rejected(self):
        result = ingest_messages(self.connection, [self._message("DMです", channel="D123")])
        self.assertEqual(result.rejected_dm, 1)
        self.assertEqual(Signal.objects.count(), 0)

    # ── 保存の範囲 ───────────────────────────────────────────
    def test_long_message_is_truncated_not_stored_in_full(self):
        ingest_messages(self.connection, [self._message("あ" * 5000)])
        signal = Signal.objects.get()
        self.assertLessEqual(len(signal.excerpt), MAX_SLACK_EXCERPT)
        self.assertLess(len(signal.excerpt), MAX_EXCERPT_LENGTH)

    def test_permalink_is_kept_so_the_original_can_be_opened(self):
        ingest_messages(self.connection, [self._message("再現しました")])
        self.assertIn("/archives/", Signal.objects.get().permalink)

    def test_conversation_classification_is_used(self):
        ingest_messages(self.connection, [self._message("再現しました")])
        self.assertEqual(
            Signal.objects.get().classification, SignalClassification.CONVERSATION
        )

    def test_resend_of_the_same_message_is_a_duplicate(self):
        message = self._message("再現しました")
        ingest_messages(self.connection, [message])
        result = ingest_messages(self.connection, [message])
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(Signal.objects.count(), 1)

    # ── 関連候補 ─────────────────────────────────────────────
    def test_feature_name_creates_a_candidate_not_a_confirmed_link(self):
        result = ingest_messages(self.connection, [self._message("受注登録が落ちます")])
        self.assertEqual(result.candidate_links, 1)

        link = WorkLink.objects.get()
        self.assertEqual(link.state, LinkState.CANDIDATE)
        self.assertEqual(link.provenance, Provenance.AI_CANDIDATE)

    def test_explicit_external_key_is_confirmed(self):
        Issue.objects.create(
            project=self.project,
            title="金額がずれる",
            severity=Severity.CRITICAL,
            external_key="DEF-42",
        )
        ingest_messages(self.connection, [self._message("DEF-42 を修正中です")])
        link = WorkLink.objects.get()
        self.assertEqual(link.state, LinkState.CONFIRMED)
        self.assertEqual(link.provenance, Provenance.EXTERNAL_ID)

    def test_wbs_code_in_the_message_is_matched(self):
        WbsTask.objects.create(project=self.project, wbs_code="3.2", name="結合試験")
        ingest_messages(self.connection, [self._message("3.2 は明日再開します")])
        link = WorkLink.objects.get()
        self.assertEqual(link.provenance, Provenance.EXTERNAL_ID)

    def test_channel_rule_creates_a_candidate_with_its_reason(self):
        self.connection.config = {
            "channels": ["C-ATLAS-QA"],
            "channel_feature_rules": {"C-ATLAS-QA": "受注登録"},
        }
        self.connection.save()
        ingest_messages(self.connection, [self._message("進捗どうですか")])
        link = WorkLink.objects.get()
        self.assertEqual(link.provenance, Provenance.RULE)
        self.assertIn("規則", link.source_reference)

    def test_unmatched_message_is_kept_without_a_link(self):
        """どこにも結び付かない Signal は削除せず残す。"""
        result = ingest_messages(self.connection, [self._message("おつかれさまです")])
        self.assertEqual(result.created, 1)
        self.assertEqual(result.candidate_links, 0)
        self.assertEqual(Signal.objects.count(), 1)

    # ── 予測への影響 ─────────────────────────────────────────
    def test_conversation_candidate_does_not_change_the_forecast(self):
        """確認前の会話由来リンクは、予測の確定根拠に使われない。"""
        ingest_messages(self.connection, [self._message("受注登録が落ちます")])
        forecast = compute_project_forecast(self.project, timezone.localdate())
        self.assertEqual(forecast.targets, ())

    def test_summary_line_reports_the_breakdown(self):
        result = ingest_messages(
            self.connection,
            [
                self._message("許可された投稿", ts="1"),
                self._message("非許可", channel="C-RANDOM", ts="2"),
                self._message("DM", channel="D1", ts="3"),
            ],
        )
        self.assertIn("非許可チャンネル 1件", result.summary_line())
        self.assertIn("DM 1件", result.summary_line())

    def test_non_slack_connection_is_ignored(self):
        connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.JIRA,
            name="Jira",
            config={"channels": ["C-ATLAS-QA"]},
        )
        result = ingest_messages(connection, [self._message("投稿")])
        self.assertEqual(result.created, 0)

    def test_freshness_uses_the_message_time(self):
        old = self._message("古い投稿", ts="9")
        ingest_messages(
            self.connection,
            [SlackMessage(**{**old.__dict__, "occurred_at": self.now - timedelta(days=3)})],
        )
        signal = Signal.objects.get()
        self.assertLess(signal.occurred_at, self.now)
