"""LDF-03 / AH-05: 再計算とスナップショットの保存。

「同一イベントの再送で予測・通知が重複せず、差分と根拠が保存される」を確認する。
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import (
    ForecastEvidence,
    ForecastSnapshot,
    Horizon,
    Signal,
    SignalClassification,
    SignalSource,
)
from apps.forecast.services.recompute import recompute_project
from apps.graph.models import MilestoneTaskLink, WorkingCalendar
from apps.projects.models import Milestone, Project, WbsTask

MONDAY = date(2026, 8, 17)
FRIDAY = date(2026, 8, 21)


class RecomputeTests(TestCase):
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
        self.milestone = Milestone.objects.create(
            project=self.project, name="結合試験完了", planned_date=FRIDAY
        )
        self.task = WbsTask.objects.create(
            project=self.project,
            wbs_code="A",
            name="結合試験",
            planned_end=FRIDAY,
            status=WbsTask.Status.IN_PROGRESS,
        )
        MilestoneTaskLink.objects.create(milestone=self.milestone, task=self.task).confirm(
            self.user
        )

    def test_first_run_creates_all_three_horizons(self):
        result = recompute_project(self.project, MONDAY)
        self.assertEqual(len(result.created), 3)
        self.assertEqual(
            {s.horizon for s in result.created},
            {Horizon.TWO_DAYS, Horizon.ONE_WEEK, Horizon.MILESTONE},
        )

    def test_rerun_without_change_creates_nothing(self):
        recompute_project(self.project, MONDAY)
        result = recompute_project(self.project, MONDAY)
        self.assertEqual(result.created, ())
        self.assertEqual(result.unchanged, 3)
        self.assertEqual(ForecastSnapshot.objects.count(), 3)

    def test_change_creates_a_new_snapshot_linked_to_the_previous(self):
        first = recompute_project(self.project, MONDAY)
        self.task.planned_end = date(2026, 8, 25)
        self.task.save()

        second = recompute_project(self.project, MONDAY)
        milestone_snapshot = next(
            s for s in second.created if s.horizon == Horizon.MILESTONE
        )
        previous = next(s for s in first.created if s.horizon == Horizon.MILESTONE)
        self.assertEqual(milestone_snapshot.previous, previous)
        self.assertEqual(milestone_snapshot.variance_business_days, 2)

    def test_worsened_reports_only_the_deterioration(self):
        recompute_project(self.project, MONDAY)
        self.task.planned_end = date(2026, 8, 25)
        self.task.save()

        result = recompute_project(self.project, MONDAY)
        self.assertTrue(result.worsened)
        self.assertTrue(all(s.variance_from_previous > 0 for s in result.worsened))

    def test_improvement_is_not_reported_as_worsening(self):
        self.task.planned_end = date(2026, 8, 25)
        self.task.save()
        recompute_project(self.project, MONDAY)

        self.task.planned_end = FRIDAY
        self.task.save()
        result = recompute_project(self.project, MONDAY)
        self.assertEqual(result.worsened, ())

    def test_becoming_undeterminable_is_detected(self):
        recompute_project(self.project, MONDAY)
        self.task.planned_end = None
        self.task.save()

        result = recompute_project(self.project, MONDAY)
        self.assertTrue(result.became_undeterminable)

    def test_evidence_is_attached_and_unusable_signals_are_marked(self):
        usable = Signal.objects.create(
            project=self.project,
            source=SignalSource.JIRA,
            external_id="D-1",
            classification=SignalClassification.DEFECT_UPDATED,
            occurred_at=timezone.now(),
            summary="修正見込みを確認",
        )
        revoked = Signal.objects.create(
            project=self.project,
            source=SignalSource.SLACK,
            external_id="S-1",
            classification=SignalClassification.CONVERSATION,
            occurred_at=timezone.now(),
            summary="削除された投稿",
            is_revoked=True,
        )
        result = recompute_project(self.project, MONDAY, evidence=[usable, revoked])
        snapshot = result.created[0]
        roles = {link.signal_id: link.role for link in snapshot.evidence_links.all()}
        self.assertEqual(roles[usable.pk], ForecastEvidence.Role.USED)
        self.assertEqual(roles[revoked.pk], ForecastEvidence.Role.UNUSED_CANDIDATE)

    def test_undeterminable_snapshot_records_missing_inputs(self):
        self.task.planned_end = None
        self.task.save()
        result = recompute_project(self.project, MONDAY)
        milestone_snapshot = next(
            s for s in result.created if s.horizon == Horizon.MILESTONE
        )
        self.assertTrue(milestone_snapshot.is_undeterminable)
        self.assertTrue(milestone_snapshot.missing_inputs)
