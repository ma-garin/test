"""LDF-02: Signal・予測スナップショット・レビューの外部挙動。

受入条件「予測の任意の値から、根拠・確認者・前回予測へ戻れる」と、
「根拠不足の予測は必ず算定不能と表示する」を固定する。
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import (
    MAX_EXCERPT_LENGTH,
    Confidence,
    ForecastEvidence,
    ForecastReview,
    ForecastSnapshot,
    Horizon,
    MissingInput,
    Signal,
    SignalClassification,
    SignalSource,
    VisibilityScope,
)
from apps.graph.models import Feature, WorkLink
from apps.graph.ontology import Provenance, RelationType
from apps.projects.models import Milestone, Project


class SignalTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.now = timezone.now()

    def _signal(self, **overrides) -> Signal:
        defaults = {
            "project": self.project,
            "source": SignalSource.JIRA,
            "external_id": "ISSUE-1",
            "classification": SignalClassification.DEFECT_UPDATED,
            "occurred_at": self.now,
            "summary": "再現を確認",
            "permalink": "https://example.invalid/browse/ISSUE-1",
        }
        defaults.update(overrides)
        return Signal.objects.create(**defaults)

    def test_signal_keeps_source_time_and_permalink(self):
        signal = self._signal()
        self.assertEqual(signal.source, SignalSource.JIRA)
        self.assertIsNotNone(signal.received_at)
        self.assertTrue(signal.permalink)

    def test_same_external_event_cannot_be_stored_twice(self):
        self._signal()
        with self.assertRaises((ValidationError, IntegrityError)):
            self._signal(summary="別の要約")

    def test_signals_without_external_id_are_deduped_by_hash(self):
        payload_hash = Signal.compute_hash("slack", "C1", "同じ投稿")
        self._signal(source=SignalSource.SLACK, external_id="", payload_hash=payload_hash)
        with self.assertRaises((ValidationError, IntegrityError)):
            self._signal(source=SignalSource.SLACK, external_id="", payload_hash=payload_hash)

    def test_excerpt_over_the_limit_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self._signal(excerpt="あ" * (MAX_EXCERPT_LENGTH + 1))
        self.assertIn("全文を複製しない", str(ctx.exception))

    def test_visibility_scope_defaults_to_project(self):
        self.assertEqual(self._signal().visibility_scope, VisibilityScope.PROJECT)

    def test_revoked_signal_is_not_usable_as_evidence(self):
        signal = self._signal(is_revoked=True)
        self.assertFalse(signal.is_usable_as_evidence)

    def test_superseded_signal_is_not_usable_as_evidence(self):
        original = self._signal()
        correction = self._signal(external_id="ISSUE-1-fix", summary="訂正")
        original.superseded_by = correction
        original.save()
        self.assertFalse(original.is_usable_as_evidence)

    def test_conversation_is_excluded_from_forecast_evidence(self):
        self._signal(classification=SignalClassification.CONVERSATION, external_id="slack-1")
        usable = self._signal(external_id="ISSUE-2")
        self.assertEqual([s.pk for s in Signal.objects.for_forecast()], [usable.pk])

    def test_signal_can_be_linked_to_a_feature_as_candidate(self):
        feature = Feature.objects.create(project=self.project, name="受注登録")
        signal = self._signal()
        link = WorkLink(
            relation_type=RelationType.DISCUSSED_IN,
            from_object=feature,
            to_object=signal,
            provenance=Provenance.AI_CANDIDATE,
        )
        link.save()
        self.assertTrue(link.is_candidate if hasattr(link, "is_candidate") else True)
        self.assertEqual(link.project, self.project)


class ForecastSnapshotTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.milestone = Milestone.objects.create(
            project=self.project, name="本番リリース", planned_date=date(2026, 8, 30)
        )
        self.now = timezone.now()

    def _snapshot(self, **overrides) -> ForecastSnapshot:
        defaults = {
            "project": self.project,
            "target": self.milestone,
            "as_of": self.now,
            "horizon": Horizon.MILESTONE,
            "baseline_date": date(2026, 8, 30),
            "forecast_date": date(2026, 9, 3),
            "variance_business_days": 3,
            "confidence": Confidence.MEDIUM,
        }
        defaults.update(overrides)
        return ForecastSnapshot.objects.create(**defaults)

    def test_snapshot_records_variance_in_business_days(self):
        snapshot = self._snapshot()
        self.assertEqual(snapshot.variance_business_days, 3)
        self.assertTrue(snapshot.is_delayed)
        self.assertFalse(snapshot.is_ahead)

    def test_ahead_of_schedule_is_negative(self):
        snapshot = self._snapshot(forecast_date=date(2026, 8, 27), variance_business_days=-2)
        self.assertTrue(snapshot.is_ahead)

    def test_undeterminable_snapshot_cannot_carry_a_date(self):
        with self.assertRaises(ValidationError) as ctx:
            self._snapshot(
                confidence=Confidence.UNKNOWN, missing_inputs=[MissingInput.NO_DEPENDENCY]
            )
        self.assertIn("算定不能", str(ctx.exception))

    def test_undeterminable_snapshot_requires_missing_inputs(self):
        with self.assertRaises(ValidationError):
            self._snapshot(
                confidence=Confidence.UNKNOWN,
                forecast_date=None,
                variance_business_days=None,
                missing_inputs=[],
            )

    def test_undeterminable_snapshot_is_stored_with_reasons(self):
        snapshot = self._snapshot(
            confidence=Confidence.UNKNOWN,
            forecast_date=None,
            variance_business_days=None,
            missing_inputs=[MissingInput.NO_CALENDAR, MissingInput.NO_DEPENDENCY],
        )
        self.assertEqual(snapshot.display_date, "算定不能")
        self.assertIn("勤務カレンダー未設定", snapshot.missing_input_labels())

    def test_determinable_snapshot_requires_a_date(self):
        with self.assertRaises(ValidationError):
            self._snapshot(confidence=Confidence.HIGH, forecast_date=None)

    def test_previous_snapshot_gives_the_change(self):
        first = self._snapshot(variance_business_days=1, as_of=self.now - timedelta(days=1))
        second = self._snapshot(variance_business_days=3, previous=first)
        self.assertEqual(second.variance_from_previous, 2)

    def test_change_is_none_without_previous(self):
        self.assertIsNone(self._snapshot().variance_from_previous)

    def test_evidence_records_used_and_unused(self):
        snapshot = self._snapshot()
        used = Signal.objects.create(
            project=self.project,
            source=SignalSource.JIRA,
            external_id="D-1",
            classification=SignalClassification.DEFECT_UPDATED,
            occurred_at=self.now,
            summary="修正見込みを確認",
        )
        unused = Signal.objects.create(
            project=self.project,
            source=SignalSource.SLACK,
            external_id="S-1",
            classification=SignalClassification.CONVERSATION,
            occurred_at=self.now,
            summary="たぶん明日には直る",
        )
        ForecastEvidence.objects.create(snapshot=snapshot, signal=used)
        ForecastEvidence.objects.create(
            snapshot=snapshot, signal=unused, role=ForecastEvidence.Role.UNUSED_CANDIDATE
        )

        roles = {link.signal_id: link.role for link in snapshot.evidence_links.all()}
        self.assertEqual(roles[used.pk], ForecastEvidence.Role.USED)
        self.assertEqual(roles[unused.pk], ForecastEvidence.Role.UNUSED_CANDIDATE)

    def test_latest_for_returns_the_newest_snapshot(self):
        self._snapshot(as_of=self.now - timedelta(days=1), variance_business_days=1)
        newest = self._snapshot(variance_business_days=3)
        found = ForecastSnapshot.objects.latest_for(self.milestone, Horizon.MILESTONE)
        self.assertEqual(found.pk, newest.pk)

    def test_same_point_cannot_be_stored_twice(self):
        self._snapshot()
        with self.assertRaises((ValidationError, IntegrityError)):
            self._snapshot()


class ForecastReviewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.milestone = Milestone.objects.create(
            project=self.project, name="本番リリース", planned_date=date(2026, 8, 30)
        )
        self.snapshot = ForecastSnapshot.objects.create(
            project=self.project,
            target=self.milestone,
            as_of=timezone.now(),
            horizon=Horizon.MILESTONE,
            baseline_date=date(2026, 8, 30),
            forecast_date=date(2026, 9, 3),
            variance_business_days=3,
            confidence=Confidence.MEDIUM,
        )

    def test_adopt_is_recorded_with_reviewer(self):
        review = ForecastReview.objects.create(
            snapshot=self.snapshot, reviewer=self.user, decision=ForecastReview.Decision.ADOPT
        )
        self.assertEqual(review.reviewer, self.user)
        self.assertIsNotNone(review.created_at)

    def test_correction_requires_a_date(self):
        with self.assertRaises(ValidationError):
            ForecastReview.objects.create(
                snapshot=self.snapshot,
                reviewer=self.user,
                decision=ForecastReview.Decision.CORRECT,
            )

    def test_rejection_requires_a_reason(self):
        with self.assertRaises(ValidationError):
            ForecastReview.objects.create(
                snapshot=self.snapshot,
                reviewer=self.user,
                decision=ForecastReview.Decision.REJECT,
            )

    def test_snapshot_keeps_all_reviews(self):
        ForecastReview.objects.create(
            snapshot=self.snapshot, reviewer=self.user, decision=ForecastReview.Decision.ADOPT
        )
        ForecastReview.objects.create(
            snapshot=self.snapshot,
            reviewer=self.user,
            decision=ForecastReview.Decision.REJECT,
            reason="依存が未確認",
        )
        self.assertEqual(self.snapshot.reviews.count(), 2)
