"""AH-06: 鮮度切れ・デバウンス・重大イベントの即時処理。

受入条件「古い情報で予測を更新し続けず、重大悪化は設定した期限内に再評価される」。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import (
    Confidence,
    Horizon,
    MissingInput,
    Signal,
    SignalClassification,
    SignalSource,
)
from apps.forecast.services.debounce import DEBOUNCE_SECONDS, decide_recompute
from apps.forecast.services.engine import compute_project_forecast
from apps.forecast.services.freshness import ProjectFreshness
from apps.graph.models import MilestoneTaskLink, WorkingCalendar
from apps.projects.models import Milestone, Project, Severity, WbsTask


class FreshnessTests(TestCase):
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
        self.now = timezone.now()

    def _signal(self, hours_ago: int, source=SignalSource.JIRA, **overrides) -> Signal:
        defaults = {
            "project": self.project,
            "source": source,
            "external_id": f"{source}-{hours_ago}",
            "classification": SignalClassification.DEFECT_UPDATED,
            "occurred_at": self.now - timedelta(hours=hours_ago),
            "summary": "更新",
        }
        defaults.update(overrides)
        return Signal.objects.create(**defaults)

    def test_no_signal_is_reported_as_missing_not_stale(self):
        freshness = ProjectFreshness.for_project(self.project, self.now)
        self.assertFalse(freshness.has_any_signal)
        self.assertFalse(freshness.is_degraded)
        self.assertIn("まだ届いていません", freshness.describe())

    def test_recent_signal_is_within_the_target(self):
        self._signal(hours_ago=1)
        freshness = ProjectFreshness.for_project(self.project, self.now)
        self.assertFalse(freshness.is_degraded)
        self.assertIn("鮮度目標の範囲内", freshness.describe())

    def test_old_signal_is_stale(self):
        self._signal(hours_ago=48)
        freshness = ProjectFreshness.for_project(self.project, self.now)
        self.assertTrue(freshness.is_degraded)
        self.assertEqual(freshness.stale_sources[0].source, SignalSource.JIRA)

    def test_describe_names_the_source_and_the_target(self):
        self._signal(hours_ago=48)
        text = ProjectFreshness.for_project(self.project, self.now).describe()
        self.assertIn("Jira", text)
        self.assertIn("目標24時間", text)

    def test_revoked_signal_does_not_count_as_fresh(self):
        self._signal(hours_ago=1, is_revoked=True)
        self._signal(hours_ago=48)
        freshness = ProjectFreshness.for_project(self.project, self.now)
        self.assertTrue(freshness.is_degraded)

    def test_each_source_has_its_own_threshold(self):
        self._signal(hours_ago=100, source=SignalSource.CONFLUENCE)
        freshness = ProjectFreshness.for_project(self.project, self.now)
        self.assertFalse(freshness.is_degraded)


class FreshnessAffectsConfidenceTests(TestCase):
    """鮮度切れは確信度を下げる。日付は消さない。"""

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
        milestone = Milestone.objects.create(
            project=self.project, name="結合試験完了", planned_date=self.today + timedelta(days=5)
        )
        task = WbsTask.objects.create(
            project=self.project,
            wbs_code="A",
            name="結合試験",
            planned_end=self.today + timedelta(days=5),
            status=WbsTask.Status.IN_PROGRESS,
        )
        MilestoneTaskLink.objects.create(milestone=milestone, task=task).confirm(self.user)
        Signal.objects.create(
            project=self.project,
            source=SignalSource.JIRA,
            external_id="old-1",
            classification=SignalClassification.DEFECT_UPDATED,
            occurred_at=timezone.now() - timedelta(hours=72),
            summary="古い更新",
        )

    def _milestone_forecast(self, freshness):
        result = compute_project_forecast(self.project, self.today, freshness=freshness)
        return result.for_horizon(Horizon.MILESTONE)[0]

    def test_stale_source_lowers_confidence(self):
        freshness = ProjectFreshness.for_project(self.project)
        forecast = self._milestone_forecast(freshness)
        self.assertEqual(forecast.confidence, Confidence.LOW)

    def test_stale_source_keeps_the_forecast_date(self):
        forecast = self._milestone_forecast(ProjectFreshness.for_project(self.project))
        self.assertIsNotNone(forecast.forecast_date)

    def test_stale_reason_is_recorded(self):
        forecast = self._milestone_forecast(ProjectFreshness.for_project(self.project))
        self.assertIn(MissingInput.STALE_SIGNAL, forecast.missing_inputs)
        self.assertIn("情報が鮮度切れ", forecast.missing_input_labels)

    def test_without_freshness_argument_nothing_is_downgraded(self):
        forecast = self._milestone_forecast(None)
        self.assertNotEqual(forecast.confidence, Confidence.LOW)


class DebounceTests(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()

    def test_first_event_runs_immediately(self):
        decision = decide_recompute(
            classification=SignalClassification.DEFECT_UPDATED, now=self.now
        )
        self.assertTrue(decision.run_now)

    def test_repeat_within_the_window_is_deferred(self):
        decision = decide_recompute(
            classification=SignalClassification.DEFECT_UPDATED,
            last_recomputed_at=self.now - timedelta(seconds=30),
            now=self.now,
        )
        self.assertTrue(decision.is_deferred)
        self.assertIsNotNone(decision.due_at)

    def test_after_the_window_it_runs_again(self):
        decision = decide_recompute(
            classification=SignalClassification.DEFECT_UPDATED,
            last_recomputed_at=self.now - timedelta(seconds=DEBOUNCE_SECONDS + 1),
            now=self.now,
        )
        self.assertTrue(decision.run_now)

    def test_critical_defect_is_not_debounced(self):
        decision = decide_recompute(
            classification=SignalClassification.DEFECT_UPDATED,
            severity=Severity.CRITICAL,
            last_recomputed_at=self.now - timedelta(seconds=1),
            now=self.now,
        )
        self.assertTrue(decision.run_now)
        self.assertIn("重大イベント", decision.reason)

    def test_new_defect_report_is_not_debounced(self):
        decision = decide_recompute(
            classification=SignalClassification.DEFECT_REPORTED,
            last_recomputed_at=self.now - timedelta(seconds=1),
            now=self.now,
        )
        self.assertTrue(decision.run_now)

    def test_schedule_update_is_not_debounced(self):
        decision = decide_recompute(
            classification=SignalClassification.SCHEDULE_UPDATE,
            last_recomputed_at=self.now - timedelta(seconds=1),
            now=self.now,
        )
        self.assertTrue(decision.run_now)

    def test_reason_is_always_explained(self):
        decision = decide_recompute(
            classification=SignalClassification.COMMIT,
            last_recomputed_at=self.now - timedelta(seconds=10),
            now=self.now,
        )
        self.assertTrue(decision.reason)
