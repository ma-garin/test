"""LDF-03: 決定論の予測エンジン。

`docs/改善に.md` 11-8:「予測エンジンは、依存なし、直列依存、分岐・合流、
クリティカルパス、循環依存、休日、前倒し、算定不能を例示データで検証する。」
この 8 条件をそのままテストにする。
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import Confidence, Horizon, MissingInput, ResolutionEstimate
from apps.forecast.services.engine import compute_project_forecast
from apps.graph.models import CalendarDay, MilestoneTaskLink, TaskDependency, WorkingCalendar
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.projects.models import Defect, Milestone, Project, Severity, WbsTask

#: 2026-08-17 は月曜。以降の平日は 17,18,19,20,21。
MONDAY = date(2026, 8, 17)
FRIDAY = date(2026, 8, 21)


class ForecastEngineTests(TestCase):
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
        self.calendar = WorkingCalendar.objects.create(project=self.project)
        self.milestone = Milestone.objects.create(
            project=self.project, name="結合試験完了", planned_date=FRIDAY
        )

    def _task(self, code: str, planned_end: date | None, **overrides) -> WbsTask:
        defaults = {
            "project": self.project,
            "wbs_code": code,
            "name": f"作業{code}",
            "planned_end": planned_end,
            "status": WbsTask.Status.IN_PROGRESS,
        }
        defaults.update(overrides)
        return WbsTask.objects.create(**defaults)

    def _require(self, task, *, confirmed: bool = True) -> MilestoneTaskLink:
        link = MilestoneTaskLink.objects.create(milestone=self.milestone, task=task)
        if confirmed:
            link.confirm(self.user)
        return link

    def _milestone_forecast(self, as_of: date = MONDAY):
        result = compute_project_forecast(self.project, as_of)
        return result.for_horizon(Horizon.MILESTONE)[0]

    # ── 1. 依存なし ──────────────────────────────────────────
    def test_single_task_without_dependency_lands_on_its_planned_end(self):
        self._require(self._task("A", FRIDAY))
        forecast = self._milestone_forecast()
        self.assertEqual(forecast.forecast_date, FRIDAY)
        self.assertEqual(forecast.variance_business_days, 0)

    # ── 2. 直列依存 ──────────────────────────────────────────
    def test_serial_dependency_pushes_the_successor(self):
        first = self._task("A", MONDAY)
        second = self._task("B", date(2026, 8, 18))
        TaskDependency.objects.create(predecessor=first, successor=second)
        self._require(second)

        # 先行が月曜に終わるので、後続は最短で火曜。計画（火曜）と同じ。
        self.assertEqual(self._milestone_forecast().forecast_date, date(2026, 8, 18))

    def test_delayed_predecessor_pushes_the_successor_beyond_its_plan(self):
        first = self._task("A", date(2026, 8, 20))
        second = self._task("B", date(2026, 8, 18))
        TaskDependency.objects.create(predecessor=first, successor=second)
        self._require(second)

        forecast = self._milestone_forecast()
        self.assertEqual(forecast.forecast_date, date(2026, 8, 21))
        self.assertEqual(forecast.variance_business_days, 0)

    def test_lag_is_counted_in_business_days(self):
        first = self._task("A", date(2026, 8, 20))  # 木曜
        second = self._task("B", date(2026, 8, 18))
        TaskDependency.objects.create(predecessor=first, successor=second, lag_business_days=2)
        self._require(second)

        # 木 +1(着手) +2(ラグ) = 火曜（週末を跨ぐ）
        self.assertEqual(self._milestone_forecast().forecast_date, date(2026, 8, 25))

    # ── 3. 分岐・合流 ────────────────────────────────────────
    def test_merge_takes_the_latest_predecessor(self):
        left = self._task("L", date(2026, 8, 18))
        right = self._task("R", date(2026, 8, 20))
        merged = self._task("M", date(2026, 8, 19))
        TaskDependency.objects.create(predecessor=left, successor=merged)
        TaskDependency.objects.create(predecessor=right, successor=merged)
        self._require(merged)

        self.assertEqual(self._milestone_forecast().forecast_date, date(2026, 8, 21))

    def test_milestone_takes_the_latest_required_task(self):
        self._require(self._task("A", date(2026, 8, 19)))
        self._require(self._task("B", date(2026, 8, 25)))

        forecast = self._milestone_forecast()
        self.assertEqual(forecast.forecast_date, date(2026, 8, 25))
        self.assertEqual(forecast.variance_business_days, 2)

    # ── 4. クリティカルパス ──────────────────────────────────
    def test_critical_path_names_the_driving_task(self):
        first = self._task("A", date(2026, 8, 20))
        second = self._task("B", date(2026, 8, 18))
        TaskDependency.objects.create(predecessor=first, successor=second)
        self._require(second)

        forecast = self._milestone_forecast()
        self.assertIn("B", forecast.critical_path)
        self.assertIn("A", forecast.critical_path)

    # ── 5. 循環依存 ──────────────────────────────────────────
    def test_cycle_stops_the_forecast(self):
        first, second = self._task("A", MONDAY), self._task("B", MONDAY)
        TaskDependency.objects.create(predecessor=first, successor=second)
        # 保存時の検証を迂回して、既存データに閉路がある状態を作る。
        TaskDependency.objects.bulk_create(
            [TaskDependency(project=self.project, predecessor=second, successor=first)]
        )
        self._require(second)

        forecast = self._milestone_forecast()
        self.assertTrue(forecast.is_undeterminable)
        self.assertIn(MissingInput.CYCLIC_DEPENDENCY, forecast.missing_inputs)

    # ── 6. 休日 ──────────────────────────────────────────────
    def test_weekend_is_skipped(self):
        first = self._task("A", date(2026, 8, 21))  # 金曜
        second = self._task("B", date(2026, 8, 21))
        TaskDependency.objects.create(predecessor=first, successor=second)
        self._require(second)

        # 金曜の翌営業日は月曜。土曜にはならない。
        self.assertEqual(self._milestone_forecast().forecast_date, date(2026, 8, 24))

    def test_registered_holiday_is_skipped(self):
        CalendarDay.objects.create(
            calendar=self.calendar, date=date(2026, 8, 24), kind=CalendarDay.Kind.HOLIDAY
        )
        first = self._task("A", date(2026, 8, 21))
        second = self._task("B", date(2026, 8, 21))
        TaskDependency.objects.create(predecessor=first, successor=second)
        self._require(second)

        self.assertEqual(self._milestone_forecast().forecast_date, date(2026, 8, 25))

    # ── 7. 前倒し ────────────────────────────────────────────
    def test_confirmed_estimate_earlier_than_plan_is_reported_as_ahead(self):
        task = self._task("A", FRIDAY)
        ResolutionEstimate.objects.create(
            target=task,
            kind=ResolutionEstimate.Kind.TASK_FINISH,
            expected_date=date(2026, 8, 19),
            confirmed_by=self.user,
            confirmed_at=timezone.now(),
        )
        self._require(task)

        forecast = self._milestone_forecast()
        self.assertEqual(forecast.variance_business_days, -2)
        self.assertIn("前倒し", forecast.summary)

    def test_completed_task_uses_its_actual_end(self):
        task = self._task(
            "A", FRIDAY, status=WbsTask.Status.DONE, actual_end=date(2026, 8, 18)
        )
        self._require(task)
        self.assertEqual(self._milestone_forecast().forecast_date, date(2026, 8, 18))

    # ── 8. 算定不能 ──────────────────────────────────────────
    def test_missing_calendar_makes_everything_undeterminable(self):
        self.calendar.delete()
        self._require(self._task("A", FRIDAY))

        forecast = self._milestone_forecast()
        self.assertTrue(forecast.is_undeterminable)
        self.assertIn(MissingInput.NO_CALENDAR, forecast.missing_inputs)

    def test_milestone_without_required_tasks_is_undeterminable(self):
        self._task("A", FRIDAY)  # 紐付けない
        forecast = self._milestone_forecast()
        self.assertTrue(forecast.is_undeterminable)
        self.assertIn(MissingInput.NO_MILESTONE_TASKS, forecast.missing_inputs)

    def test_task_without_planned_end_is_undeterminable(self):
        self._require(self._task("A", None))
        forecast = self._milestone_forecast()
        self.assertTrue(forecast.is_undeterminable)
        self.assertIn(MissingInput.NO_PLANNED_END, forecast.missing_inputs)

    def test_unresolved_blocker_without_estimate_is_undeterminable(self):
        task = self._task("A", FRIDAY, status=WbsTask.Status.BLOCKED)
        defect = Defect.objects.create(
            project=self.project, title="再現する", severity=Severity.CRITICAL
        )
        WorkLink(
            relation_type=RelationType.BLOCKS,
            from_object=defect,
            to_object=task,
            provenance=Provenance.MANUAL,
            state=LinkState.CONFIRMED,
        ).save()
        self._require(task)

        forecast = self._milestone_forecast()
        self.assertTrue(forecast.is_undeterminable)
        self.assertIn(MissingInput.UNRESOLVED_BLOCKER, forecast.missing_inputs)

    def test_blocker_with_confirmed_retest_date_is_used(self):
        task = self._task("A", date(2026, 8, 18), status=WbsTask.Status.BLOCKED)
        defect = Defect.objects.create(
            project=self.project, title="再現する", severity=Severity.CRITICAL
        )
        WorkLink(
            relation_type=RelationType.BLOCKS,
            from_object=defect,
            to_object=task,
            provenance=Provenance.MANUAL,
            state=LinkState.CONFIRMED,
        ).save()
        ResolutionEstimate.objects.create(
            target=defect,
            kind=ResolutionEstimate.Kind.RETEST,
            expected_date=date(2026, 8, 25),
            confirmed_by=self.user,
            confirmed_at=timezone.now(),
        )
        self._require(task)

        forecast = self._milestone_forecast()
        self.assertEqual(forecast.forecast_date, date(2026, 8, 25))
        self.assertEqual(forecast.variance_business_days, 2)

    def test_candidate_blocker_link_is_not_used_for_days(self):
        """未確認の候補リンクで納期を動かさない。"""
        task = self._task("A", FRIDAY, status=WbsTask.Status.BLOCKED)
        defect = Defect.objects.create(
            project=self.project, title="たぶん関係ある", severity=Severity.HIGH
        )
        WorkLink(
            relation_type=RelationType.BLOCKS,
            from_object=defect,
            to_object=task,
            provenance=Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
        ).save()
        self._require(task)

        forecast = self._milestone_forecast()
        self.assertFalse(forecast.is_undeterminable)
        self.assertEqual(forecast.forecast_date, FRIDAY)

    # ── 確信度 ───────────────────────────────────────────────
    def test_unconfirmed_milestone_link_lowers_confidence(self):
        self._require(self._task("A", FRIDAY), confirmed=False)
        self.assertEqual(self._milestone_forecast().confidence, Confidence.LOW)

    def test_confirmed_estimate_and_links_give_high_confidence(self):
        task = self._task("A", FRIDAY)
        ResolutionEstimate.objects.create(
            target=task,
            kind=ResolutionEstimate.Kind.TASK_FINISH,
            expected_date=FRIDAY,
            confirmed_by=self.user,
            confirmed_at=timezone.now(),
        )
        self._require(task)
        self.assertEqual(self._milestone_forecast().confidence, Confidence.HIGH)


class HorizonTests(TestCase):
    """2日後・1週間後は「その時点で何が残るか」を出す。"""

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
            project=self.project, name="結合試験完了", planned_date=date(2026, 8, 28)
        )
        self.near = WbsTask.objects.create(
            project=self.project,
            wbs_code="A",
            name="原因切り分け",
            planned_end=date(2026, 8, 19),
            status=WbsTask.Status.IN_PROGRESS,
        )
        self.far = WbsTask.objects.create(
            project=self.project,
            wbs_code="B",
            name="総合試験",
            planned_end=date(2026, 8, 28),
            status=WbsTask.Status.IN_PROGRESS,
        )
        for task in (self.near, self.far):
            MilestoneTaskLink.objects.create(milestone=self.milestone, task=task).confirm(
                self.user
            )

    def _horizon(self, horizon):
        return compute_project_forecast(self.project, MONDAY).for_horizon(horizon)[0]

    def test_two_day_horizon_reports_the_next_completion(self):
        forecast = self._horizon(Horizon.TWO_DAYS)
        self.assertEqual(forecast.forecast_date, date(2026, 8, 19))
        self.assertIn("原因切り分け", forecast.summary)

    def test_two_day_horizon_counts_remaining_required_tasks(self):
        forecast = self._horizon(Horizon.TWO_DAYS)
        self.assertIn("B", forecast.blockers)

    def test_one_week_horizon_uses_five_business_days(self):
        forecast = self._horizon(Horizon.ONE_WEEK)
        self.assertIn("8/24", forecast.summary)

    def test_all_three_horizons_are_produced(self):
        result = compute_project_forecast(self.project, MONDAY)
        self.assertEqual(len(result.targets), 3)
        self.assertEqual(
            {item.horizon for item in result.targets},
            {Horizon.TWO_DAYS, Horizon.ONE_WEEK, Horizon.MILESTONE},
        )

    def test_finished_tasks_only_gives_a_clear_horizon(self):
        for task in (self.near, self.far):
            task.status = WbsTask.Status.DONE
            task.actual_end = date(2026, 8, 18)
            task.save()
        forecast = self._horizon(Horizon.TWO_DAYS)
        self.assertEqual(forecast.confidence, Confidence.HIGH)
        self.assertIn("未完了の必須WBSはありません", forecast.summary)
