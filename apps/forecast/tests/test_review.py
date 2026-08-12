"""AH-07: 予測・関連候補への人のレビューと、実績フィードバック。

受入条件「採用／修正／却下と予測誤差が次回の評価に使える」。
AI が確定させたように見せないこと、算定不能を採用できないことも固定する。
"""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import (
    Confidence,
    ForecastReview,
    ForecastSnapshot,
    Horizon,
    MissingInput,
    Signal,
    SignalClassification,
    SignalSource,
)
from apps.forecast.services.review import (
    ReviewError,
    accuracy_report,
    record_review,
    review_link,
)
from apps.graph.models import Feature, WorkingCalendar
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.projects.models import Milestone, Project


class ReviewTests(TestCase):
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
        WorkingCalendar.objects.create(project=self.project)
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
        self.client.force_login(self.user)

    # ── 予測へのレビュー ─────────────────────────────────────
    def test_adopt_is_recorded_with_reviewer_and_time(self):
        review = record_review(
            self.snapshot, self.user, decision=ForecastReview.Decision.ADOPT
        )
        self.assertEqual(review.reviewer, self.user)
        self.assertIsNotNone(review.created_at)

    def test_correction_keeps_the_original_forecast(self):
        record_review(
            self.snapshot,
            self.user,
            decision=ForecastReview.Decision.CORRECT,
            corrected_date=date(2026, 9, 1),
        )
        self.snapshot.refresh_from_db()
        self.assertEqual(self.snapshot.forecast_date, date(2026, 9, 3))
        self.assertEqual(self.snapshot.reviews.first().corrected_date, date(2026, 9, 1))

    def test_undeterminable_forecast_cannot_be_adopted(self):
        undeterminable = ForecastSnapshot.objects.create(
            project=self.project,
            target=self.milestone,
            as_of=timezone.now() + timedelta(minutes=1),
            horizon=Horizon.TWO_DAYS,
            confidence=Confidence.UNKNOWN,
            missing_inputs=[MissingInput.NO_DEPENDENCY],
        )
        with self.assertRaises(ReviewError):
            record_review(undeterminable, self.user, decision=ForecastReview.Decision.ADOPT)

    def test_undeterminable_forecast_can_be_rejected(self):
        undeterminable = ForecastSnapshot.objects.create(
            project=self.project,
            target=self.milestone,
            as_of=timezone.now() + timedelta(minutes=2),
            horizon=Horizon.ONE_WEEK,
            confidence=Confidence.UNKNOWN,
            missing_inputs=[MissingInput.NO_DEPENDENCY],
        )
        review = record_review(
            undeterminable,
            self.user,
            decision=ForecastReview.Decision.REJECT,
            reason="依存を登録してから再評価する",
        )
        self.assertEqual(review.decision, ForecastReview.Decision.REJECT)

    # ── 関連候補のレビュー ───────────────────────────────────
    def test_candidate_link_becomes_evidence_only_after_confirmation(self):
        feature = Feature.objects.create(project=self.project, name="受注登録")
        signal = Signal.objects.create(
            project=self.project,
            source=SignalSource.SLACK,
            external_id="s-1",
            classification=SignalClassification.CONVERSATION,
            occurred_at=timezone.now(),
            summary="たぶんこの機能",
        )
        link = WorkLink(
            relation_type=RelationType.DISCUSSED_IN,
            from_object=feature,
            to_object=signal,
            provenance=Provenance.AI_CANDIDATE,
        )
        link.save()
        self.assertEqual(link.state, LinkState.CANDIDATE)

        review_link(link, self.user, confirm=True, reason="QAが確認")
        link.refresh_from_db()
        self.assertEqual(link.state, LinkState.CONFIRMED)
        self.assertEqual(link.confirmed_by, self.user)
        self.assertEqual(link.review_reason, "QAが確認")

    def test_rejected_link_keeps_the_record(self):
        feature = Feature.objects.create(project=self.project, name="在庫引当")
        signal = Signal.objects.create(
            project=self.project,
            source=SignalSource.SLACK,
            external_id="s-2",
            classification=SignalClassification.CONVERSATION,
            occurred_at=timezone.now(),
            summary="別機能の話だった",
        )
        link = WorkLink(
            relation_type=RelationType.DISCUSSED_IN,
            from_object=feature,
            to_object=signal,
            provenance=Provenance.AI_CANDIDATE,
        )
        link.save()
        review_link(link, self.user, confirm=False, reason="無関係")
        link.refresh_from_db()
        self.assertEqual(link.state, LinkState.REJECTED)
        self.assertEqual(WorkLink.objects.filter(pk=link.pk).count(), 1)

    # ── 実績フィードバック ───────────────────────────────────
    def test_error_is_none_until_the_actual_date_arrives(self):
        report = accuracy_report(self.project)
        self.assertIsNone(report.mean_absolute_error)
        self.assertFalse(report.rows[0].is_measurable)

    def test_error_is_measured_against_the_actual_date(self):
        self.milestone.actual_date = date(2026, 9, 5)
        self.milestone.save()
        report = accuracy_report(self.project)
        self.assertEqual(report.rows[0].error_days, 2)
        self.assertEqual(report.mean_absolute_error, 2.0)

    def test_review_counts_are_separated_by_decision(self):
        record_review(self.snapshot, self.user, decision=ForecastReview.Decision.ADOPT)
        record_review(
            self.snapshot,
            self.user,
            decision=ForecastReview.Decision.REJECT,
            reason="依存が未確認",
        )
        report = accuracy_report(self.project)
        self.assertEqual(report.adopted, 1)
        self.assertEqual(report.rejected, 1)
        self.assertEqual(report.reviewed_total, 2)

    def test_unreviewed_snapshots_are_counted(self):
        report = accuracy_report(self.project)
        self.assertEqual(report.unreviewed, 1)
        self.assertEqual(report.review_rate, 0.0)

    # ── 画面からの操作 ───────────────────────────────────────
    def test_review_endpoint_records_the_decision(self):
        response = self.client.post(
            reverse("forecast:review_snapshot", args=[self.snapshot.pk]),
            {"decision": ForecastReview.Decision.ADOPT, "next": "/forecast/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot.reviews.count(), 1)

    def test_review_endpoint_rejects_get(self):
        response = self.client.get(
            reverse("forecast:review_snapshot", args=[self.snapshot.pk])
        )
        self.assertEqual(response.status_code, 405)

    def test_review_endpoint_rejects_an_unknown_decision(self):
        self.client.post(
            reverse("forecast:review_snapshot", args=[self.snapshot.pk]),
            {"decision": "approve_everything", "next": "/forecast/"},
        )
        self.assertEqual(self.snapshot.reviews.count(), 0)

    def test_other_tenant_snapshot_cannot_be_reviewed(self):
        other_tenant = Tenant.objects.create(code="beta", name="BETA")
        other_project = Project.objects.create(tenant=other_tenant, code="x1", name="別")
        other_milestone = Milestone.objects.create(
            project=other_project, name="別テナント", planned_date=date(2026, 8, 30)
        )
        foreign = ForecastSnapshot.objects.create(
            project=other_project,
            target=other_milestone,
            as_of=timezone.now(),
            horizon=Horizon.MILESTONE,
            baseline_date=date(2026, 8, 30),
            forecast_date=date(2026, 9, 3),
            variance_business_days=3,
            confidence=Confidence.MEDIUM,
        )
        response = self.client.post(
            reverse("forecast:review_snapshot", args=[foreign.pk]), {"decision": "adopt"}
        )
        self.assertEqual(response.status_code, 404)
