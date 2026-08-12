"""AH-05: 冪等なイベント処理。

受入条件「同一イベントの再送で予測・通知が重複せず、差分と根拠が保存される」を、
再送・順不同・欠落・失敗の 4 通りで確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import (
    ForecastSnapshot,
    InboundEvent,
    Signal,
    SignalClassification,
    SignalSource,
)
from apps.forecast.services.ingest import IngestError, receive_event, revoke_signal
from apps.graph.models import MilestoneTaskLink, WorkingCalendar
from apps.projects.models import Milestone, Project, WbsTask


class IngestTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pm",
            email="pm@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        WorkingCalendar.objects.create(project=self.project)
        self.today = timezone.localdate()
        self.milestone = Milestone.objects.create(
            project=self.project, name="結合試験完了", planned_date=self.today + timedelta(days=5)
        )
        self.task = WbsTask.objects.create(
            project=self.project,
            wbs_code="3.2",
            name="結合試験",
            planned_end=self.today + timedelta(days=5),
            status=WbsTask.Status.IN_PROGRESS,
        )
        MilestoneTaskLink.objects.create(milestone=self.milestone, task=self.task).confirm(
            self.user
        )
        self.now = timezone.now()

    def _receive(self, **overrides):
        defaults = {
            "source": SignalSource.JIRA,
            "event_type": "issue_updated",
            "occurred_at": self.now,
            "payload": {"key": "DEF-42", "status": "修正中"},
            "external_event_id": "evt-1",
            "summary": "不具合 DEF-42 を修正中へ更新",
            "classification": SignalClassification.DEFECT_UPDATED,
            "permalink": "https://example.invalid/browse/DEF-42",
        }
        defaults.update(overrides)
        return receive_event(self.project, **defaults)

    # ── 受付と正規化 ─────────────────────────────────────────
    def test_event_is_stored_and_normalized_into_a_signal(self):
        result = self._receive()
        self.assertEqual(result.event.status, InboundEvent.Status.PROCESSED)
        self.assertIsNotNone(result.signal)
        self.assertEqual(result.signal.permalink, "https://example.invalid/browse/DEF-42")

    def test_signal_keeps_source_and_occurrence_time(self):
        signal = self._receive().signal
        self.assertEqual(signal.source, SignalSource.JIRA)
        self.assertEqual(signal.occurred_at, self.now)

    # ── 冪等性 ───────────────────────────────────────────────
    def test_resend_with_same_external_id_creates_no_new_signal(self):
        first = self._receive()
        second = self._receive()

        self.assertTrue(second.is_duplicate)
        self.assertEqual(Signal.objects.count(), 1)
        self.assertEqual(second.signal, first.signal)

    def test_resend_is_recorded_not_discarded(self):
        self._receive()
        self._receive()
        duplicates = InboundEvent.objects.filter(status=InboundEvent.Status.DUPLICATE)
        self.assertEqual(duplicates.count(), 1)
        self.assertIsNotNone(duplicates.first().duplicate_of)

    def test_resend_without_external_id_is_deduped_by_payload(self):
        self._receive(external_event_id="")
        second = self._receive(external_event_id="")
        self.assertTrue(second.is_duplicate)
        self.assertEqual(Signal.objects.count(), 1)

    def test_resend_creates_no_extra_snapshot(self):
        self._receive()
        count_after_first = ForecastSnapshot.objects.count()
        self._receive()
        self.assertEqual(ForecastSnapshot.objects.count(), count_after_first)

    def test_resend_produces_no_new_notification(self):
        self._receive()
        second = self._receive()
        self.assertEqual(second.notifications, ())

    def test_different_payload_is_not_a_duplicate(self):
        self._receive()
        second = self._receive(
            external_event_id="evt-2", payload={"key": "DEF-42", "status": "確認中"}
        )
        self.assertFalse(second.is_duplicate)
        self.assertEqual(Signal.objects.count(), 2)

    # ── 再計算と根拠 ─────────────────────────────────────────
    def test_recompute_records_the_signal_as_evidence(self):
        result = self._receive()
        snapshot = result.created_snapshots[0]
        self.assertIn(result.signal.pk, [s.pk for s in snapshot.evidence.all()])

    def test_conversation_does_not_trigger_recompute(self):
        """会話は候補であり、状態・期日を確定しない。"""
        result = self._receive(
            source=SignalSource.SLACK,
            classification=SignalClassification.CONVERSATION,
            external_event_id="slack-1",
        )
        self.assertIsNone(result.recompute)
        self.assertIsNotNone(result.signal)

    def test_worsening_is_reported_as_a_notification(self):
        self._receive()
        self.task.planned_end = self.today + timedelta(days=12)
        self.task.save()
        result = self._receive(
            external_event_id="evt-2", payload={"key": "DEF-42", "status": "再試験待ち"}
        )
        self.assertTrue(result.notifications)

    def test_unchanged_forecast_produces_no_notification(self):
        self._receive()
        result = self._receive(
            external_event_id="evt-3", payload={"key": "DEF-42", "status": "同じ着地"}
        )
        self.assertEqual(result.notifications, ())

    # ── 失敗と訂正 ───────────────────────────────────────────
    def test_unknown_source_is_recorded_as_failed(self):
        with self.assertRaises(IngestError):
            self._receive(source="unknown_tool", external_event_id="evt-x")

        event = InboundEvent.objects.get(external_event_id="evt-x")
        self.assertEqual(event.status, InboundEvent.Status.FAILED)
        self.assertIn("未対応の情報源", event.error_reason)

    def test_failure_does_not_create_a_signal(self):
        with self.assertRaises(IngestError):
            self._receive(classification="not_a_classification", external_event_id="evt-y")
        self.assertFalse(Signal.objects.filter(external_id="evt-y").exists())

    def test_revoked_signal_stops_being_usable_evidence(self):
        signal = self._receive().signal
        revoke_signal(signal, reason="外部で削除された")
        signal.refresh_from_db()
        self.assertTrue(signal.is_revoked)
        self.assertFalse(signal.is_usable_as_evidence)

    # ── テナント境界 ─────────────────────────────────────────
    def test_event_is_scoped_to_its_project(self):
        other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        self._receive()
        self.assertEqual(InboundEvent.objects.filter(project=other).count(), 0)

    def test_same_external_id_in_another_project_is_not_a_duplicate(self):
        other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        WorkingCalendar.objects.create(project=other)
        self._receive()
        result = receive_event(
            other,
            source=SignalSource.JIRA,
            event_type="issue_updated",
            occurred_at=self.now,
            payload={"key": "DEF-42", "status": "修正中"},
            external_event_id="evt-1",
            summary="別案件の同じキー",
            classification=SignalClassification.DEFECT_UPDATED,
        )
        self.assertFalse(result.is_duplicate)


class OutOfOrderTests(TestCase):
    """Webhook は順不同で届く。古いイベントで新しい事実を上書きしない。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        WorkingCalendar.objects.create(project=self.project)
        self.now = timezone.now()

    def test_older_event_keeps_its_own_occurrence_time(self):
        newer = receive_event(
            self.project,
            source=SignalSource.JIRA,
            event_type="issue_updated",
            occurred_at=self.now,
            payload={"v": 2},
            external_event_id="evt-2",
            summary="新しい更新",
            classification=SignalClassification.DEFECT_UPDATED,
        )
        older = receive_event(
            self.project,
            source=SignalSource.JIRA,
            event_type="issue_updated",
            occurred_at=self.now - timedelta(hours=2),
            payload={"v": 1},
            external_event_id="evt-1",
            summary="古い更新",
            classification=SignalClassification.DEFECT_UPDATED,
        )
        self.assertLess(older.signal.occurred_at, newer.signal.occurred_at)
        latest = Signal.objects.order_by("-occurred_at").first()
        self.assertEqual(latest.pk, newer.signal.pk)

    def test_forecast_without_milestone_is_undeterminable_not_guessed(self):
        result = receive_event(
            self.project,
            source=SignalSource.JIRA,
            event_type="issue_updated",
            occurred_at=self.now,
            payload={"v": 1},
            external_event_id="evt-1",
            summary="更新",
            classification=SignalClassification.DEFECT_UPDATED,
        )
        self.assertEqual(result.created_snapshots, ())
        self.assertEqual(ForecastSnapshot.objects.count(), 0)
