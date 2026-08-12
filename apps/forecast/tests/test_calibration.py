"""LDF-09: 確信度の校正と、AI 候補の採否・寄与の集計。

受入条件「AI 候補の採否・誤差・実績が追え、閾値や規則の改善に使える」。
数字を出してよい条件（サンプル数）と、出してはいけない条件を固定する。
"""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import Confidence, ForecastSnapshot, Horizon
from apps.forecast.services.calibration import (
    SAMPLE_SHORTAGE_LABEL,
    build_calibration,
)
from apps.graph.models import WorkingCalendar
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.projects.models import Defect, Milestone, Project, Severity, WbsTask


class CalibrationTestCase(TestCase):
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
        self.now = timezone.now()
        self._sequence = 0

    # ── 補助 ────────────────────────────────────────────────
    def _milestone(self, name: str, *, actual: date | None, project=None) -> Milestone:
        return Milestone.objects.create(
            project=project or self.project,
            name=name,
            planned_date=date(2026, 8, 30),
            actual_date=actual,
        )

    def _snapshot(
        self,
        target,
        *,
        confidence: str,
        forecast_date: date,
        horizon: str = Horizon.MILESTONE,
        as_of=None,
        project=None,
    ) -> ForecastSnapshot:
        self._sequence += 1
        return ForecastSnapshot.objects.create(
            project=project or self.project,
            target=target,
            as_of=as_of or (self.now + timedelta(minutes=self._sequence)),
            horizon=horizon,
            baseline_date=date(2026, 8, 30),
            forecast_date=forecast_date,
            variance_business_days=0,
            confidence=confidence,
        )

    def _fill_bucket(self, confidence: str, *, error_days: int, count: int) -> Milestone:
        """同じ誤差の予測を `count` 件積む。実績日は共通のマイルストーンに置く。"""

        actual = date(2026, 9, 1)
        milestone = self._milestone(f"{confidence}-{error_days}", actual=actual)
        for _ in range(count):
            self._snapshot(
                milestone,
                confidence=confidence,
                forecast_date=actual - timedelta(days=error_days),
            )
        return milestone

    def _ai_link(self, *, code: str) -> WorkLink:
        task = WbsTask.objects.create(
            project=self.project,
            wbs_code=code,
            name=f"作業{code}",
            planned_end=date(2026, 8, 20),
            status=WbsTask.Status.IN_PROGRESS,
        )
        defect = Defect.objects.create(
            project=self.project, title=f"不具合{code}", severity=Severity.HIGH
        )
        link = WorkLink(
            relation_type=RelationType.BLOCKS,
            from_object=defect,
            to_object=task,
            provenance=Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
        )
        link.save()
        link.task = task
        return link


class ConfidenceCalibrationTests(CalibrationTestCase):
    def test_below_minimum_samples_reports_shortage_and_hides_numbers(self):
        self._fill_bucket(Confidence.HIGH, error_days=7, count=3)

        report = build_calibration(self.project, minimum_samples=10)
        bucket = report.bucket(Confidence.HIGH)

        self.assertEqual(bucket.sample_size, 3)
        self.assertFalse(bucket.is_sufficient)
        self.assertIsNone(bucket.mean_absolute_error)
        self.assertIsNone(bucket.mean_signed_error)
        self.assertIsNone(bucket.hit_rate)
        self.assertEqual(bucket.status_label, SAMPLE_SHORTAGE_LABEL)
        self.assertEqual(bucket.shortage, 7)
        self.assertFalse(report.is_calibratable)

    def test_shortage_suggestion_contains_no_error_figure(self):
        self._fill_bucket(Confidence.LOW, error_days=30, count=2)

        report = build_calibration(self.project, minimum_samples=10)
        message = next(m for m in report.suggestions if "低" in m)

        self.assertIn(SAMPLE_SHORTAGE_LABEL, message)
        self.assertNotIn("30", message)

    def test_sufficient_samples_produce_mean_absolute_error(self):
        self._fill_bucket(Confidence.HIGH, error_days=4, count=10)

        bucket = build_calibration(self.project, minimum_samples=10).bucket(Confidence.HIGH)

        self.assertTrue(bucket.is_sufficient)
        self.assertEqual(bucket.mean_absolute_error, 4.0)
        self.assertEqual(bucket.mean_signed_error, 4.0)
        self.assertEqual(bucket.hit_count, 0)

    def test_errors_are_bucketed_by_confidence(self):
        self._fill_bucket(Confidence.HIGH, error_days=1, count=10)
        self._fill_bucket(Confidence.LOW, error_days=12, count=10)

        report = build_calibration(self.project, minimum_samples=10)

        self.assertEqual(report.bucket(Confidence.HIGH).mean_absolute_error, 1.0)
        self.assertEqual(report.bucket(Confidence.LOW).mean_absolute_error, 12.0)
        self.assertEqual(report.bucket(Confidence.MEDIUM).sample_size, 0)
        self.assertEqual(report.total_samples, 20)

    def test_milestone_without_actual_date_is_not_sampled(self):
        milestone = self._milestone("未着地", actual=None)
        self._snapshot(
            milestone, confidence=Confidence.HIGH, forecast_date=date(2026, 9, 5)
        )

        report = build_calibration(self.project, minimum_samples=1)

        self.assertEqual(report.total_samples, 0)
        self.assertIsNone(report.bucket(Confidence.HIGH).mean_absolute_error)

    def test_early_prediction_is_reported_as_negative_bias(self):
        self._fill_bucket(Confidence.MEDIUM, error_days=-6, count=10)

        bucket = build_calibration(self.project, minimum_samples=10).bucket(Confidence.MEDIUM)

        self.assertEqual(bucket.mean_signed_error, -6.0)
        self.assertEqual(bucket.mean_absolute_error, 6.0)

    def test_tolerance_breach_produces_threshold_suggestion(self):
        self._fill_bucket(Confidence.HIGH, error_days=9, count=10)

        suggestions = build_calibration(self.project, minimum_samples=10).suggestions

        self.assertTrue(any("閾値" in message for message in suggestions))
        self.assertTrue(all(isinstance(message, str) for message in suggestions))

    def test_inverted_ordering_between_confidences_is_flagged(self):
        self._fill_bucket(Confidence.HIGH, error_days=15, count=10)
        self._fill_bucket(Confidence.LOW, error_days=1, count=10)

        suggestions = build_calibration(self.project, minimum_samples=10).suggestions

        self.assertTrue(any("逆転" in message for message in suggestions))

    def test_suggestions_do_not_modify_snapshots(self):
        milestone = self._fill_bucket(Confidence.HIGH, error_days=20, count=10)
        before = list(
            ForecastSnapshot.objects.filter(project=self.project)
            .order_by("as_of")
            .values_list("forecast_date", "confidence")
        )

        report = build_calibration(self.project, minimum_samples=10)

        self.assertTrue(report.suggestions)
        after = list(
            ForecastSnapshot.objects.filter(project=self.project)
            .order_by("as_of")
            .values_list("forecast_date", "confidence")
        )
        self.assertEqual(before, after)
        milestone.refresh_from_db()
        self.assertEqual(milestone.forecast_date, None)

    def test_minimum_samples_must_be_positive(self):
        with self.assertRaises(ValueError):
            build_calibration(self.project, minimum_samples=0)


class CandidateAdoptionTests(CalibrationTestCase):
    def test_states_are_counted_separately(self):
        self._ai_link(code="A")
        self._ai_link(code="B").confirm(self.user)
        self._ai_link(code="C").reject(self.user, "無関係")

        candidates = build_calibration(self.project, minimum_samples=10).candidates

        self.assertEqual(candidates.pending, 1)
        self.assertEqual(candidates.confirmed, 1)
        self.assertEqual(candidates.rejected, 1)
        self.assertEqual(candidates.total, 3)
        self.assertEqual(candidates.confirmation_rate, 0.5)

    def test_confirmed_candidate_without_later_forecast_is_not_effective(self):
        self._ai_link(code="A").confirm(self.user)

        candidates = build_calibration(self.project, minimum_samples=10).candidates

        self.assertEqual(candidates.confirmed, 1)
        self.assertEqual(candidates.effective, 0)
        self.assertEqual(candidates.confirmed_without_effect, 1)
        self.assertEqual(candidates.effect_rate, 0.0)

    def test_forecast_after_confirmation_counts_as_effective(self):
        link = self._ai_link(code="A")
        link.confirm(self.user)
        self._snapshot(
            link.task,
            confidence=Confidence.MEDIUM,
            forecast_date=date(2026, 8, 25),
            horizon=Horizon.TWO_DAYS,
            as_of=link.confirmed_at + timedelta(minutes=5),
        )

        candidates = build_calibration(self.project, minimum_samples=10).candidates

        self.assertEqual(candidates.effective, 1)
        self.assertEqual(candidates.confirmed_without_effect, 0)
        self.assertEqual(candidates.effect_rate, 1.0)

    def test_forecast_before_confirmation_does_not_count_as_effective(self):
        link = self._ai_link(code="A")
        link.confirm(self.user)
        self._snapshot(
            link.task,
            confidence=Confidence.MEDIUM,
            forecast_date=date(2026, 8, 25),
            horizon=Horizon.TWO_DAYS,
            as_of=link.confirmed_at - timedelta(hours=1),
        )

        candidates = build_calibration(self.project, minimum_samples=10).candidates

        self.assertEqual(candidates.effective, 0)
        self.assertEqual(candidates.confirmed_without_effect, 1)

    def test_low_effect_rate_is_reported_separately_from_adoption(self):
        for code in ("A", "B", "C"):
            self._ai_link(code=code).confirm(self.user)

        report = build_calibration(self.project, minimum_samples=10)

        self.assertEqual(report.candidates.confirmation_rate, 1.0)
        self.assertEqual(report.candidates.effect_rate, 0.0)
        self.assertTrue(any("寄与" in message for message in report.suggestions))

    def test_pending_candidates_are_reported(self):
        self._ai_link(code="A")

        suggestions = build_calibration(self.project, minimum_samples=10).suggestions

        self.assertTrue(any("未確認" in message for message in suggestions))

    def test_manual_links_are_not_counted_as_ai_candidates(self):
        task = WbsTask.objects.create(
            project=self.project,
            wbs_code="M",
            name="手動",
            planned_end=date(2026, 8, 20),
            status=WbsTask.Status.IN_PROGRESS,
        )
        defect = Defect.objects.create(
            project=self.project, title="手動登録", severity=Severity.HIGH
        )
        WorkLink(
            relation_type=RelationType.BLOCKS,
            from_object=defect,
            to_object=task,
            provenance=Provenance.MANUAL,
            state=LinkState.CONFIRMED,
        ).save()

        candidates = build_calibration(self.project, minimum_samples=10).candidates

        self.assertEqual(candidates.total, 0)
        self.assertIsNone(candidates.confirmation_rate)
        self.assertIsNone(candidates.effect_rate)


class ProjectBoundaryTests(CalibrationTestCase):
    def test_other_project_data_is_not_mixed(self):
        other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        WorkingCalendar.objects.create(project=other)
        other_milestone = self._milestone("他案件", actual=date(2026, 9, 1), project=other)
        for _ in range(10):
            self._snapshot(
                other_milestone,
                confidence=Confidence.HIGH,
                forecast_date=date(2026, 8, 1),
                project=other,
            )
        self._fill_bucket(Confidence.HIGH, error_days=1, count=10)

        report = build_calibration(self.project, minimum_samples=10)

        self.assertEqual(report.total_samples, 10)
        self.assertEqual(report.bucket(Confidence.HIGH).mean_absolute_error, 1.0)

    def test_other_project_ai_candidates_are_not_counted(self):
        other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        task = WbsTask.objects.create(
            project=other,
            wbs_code="X",
            name="他案件の作業",
            planned_end=date(2026, 8, 20),
            status=WbsTask.Status.IN_PROGRESS,
        )
        defect = Defect.objects.create(
            project=other, title="他案件の不具合", severity=Severity.HIGH
        )
        WorkLink(
            relation_type=RelationType.BLOCKS,
            from_object=defect,
            to_object=task,
            provenance=Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
        ).save()
        self._ai_link(code="A")

        candidates = build_calibration(self.project, minimum_samples=10).candidates

        self.assertEqual(candidates.total, 1)
        self.assertEqual(candidates.pending, 1)
