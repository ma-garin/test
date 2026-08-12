"""GE-02: 有向依存・マイルストーン紐付け・勤務日の外部挙動テスト。

`docs/改善に.md` の受入条件「直列・分岐・合流・循環依存をテストし、予測可能な DAG
だけを計算へ渡せる」に対応する。
"""

from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.graph.models import (
    CalendarDay,
    DependencyCycleError,
    MilestoneTaskLink,
    TaskDependency,
    WorkingCalendar,
)
from apps.graph.services.business_days import BusinessCalendar
from apps.projects.models import Milestone, Project, WbsTask


class DependencyTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        self.tasks = {
            code: WbsTask.objects.create(project=self.project, wbs_code=code, name=f"作業{code}")
            for code in ("A", "B", "C", "D")
        }

    def _depend(self, predecessor: str, successor: str, **kwargs) -> TaskDependency:
        return TaskDependency.objects.create(
            predecessor=self.tasks[predecessor], successor=self.tasks[successor], **kwargs
        )

    def test_serial_chain_is_allowed(self):
        self._depend("A", "B")
        self._depend("B", "C")
        self.assertEqual(TaskDependency.objects.count(), 2)

    def test_branch_and_merge_are_allowed(self):
        self._depend("A", "B")
        self._depend("A", "C")
        self._depend("B", "D")
        self._depend("C", "D")
        self.assertEqual(self.tasks["D"].predecessor_links.count(), 2)

    def test_direct_cycle_is_rejected(self):
        self._depend("A", "B")
        with self.assertRaises(DependencyCycleError):
            self._depend("B", "A")

    def test_indirect_cycle_is_rejected(self):
        self._depend("A", "B")
        self._depend("B", "C")
        with self.assertRaises(DependencyCycleError) as ctx:
            self._depend("C", "A")
        self.assertIn("A", ctx.exception.path)

    def test_cycle_error_shows_wbs_codes(self):
        self._depend("A", "B")
        with self.assertRaises(DependencyCycleError) as ctx:
            self._depend("B", "A")
        self.assertEqual(ctx.exception.path[0], ctx.exception.path[-1])

    def test_self_dependency_is_rejected(self):
        with self.assertRaises(ValidationError):
            TaskDependency.objects.create(
                predecessor=self.tasks["A"], successor=self.tasks["A"]
            )

    def test_cross_project_dependency_is_rejected(self):
        foreign = WbsTask.objects.create(project=self.other, wbs_code="X", name="別案件")
        with self.assertRaises(ValidationError):
            TaskDependency.objects.create(predecessor=self.tasks["A"], successor=foreign)

    def test_duplicate_dependency_is_rejected(self):
        self._depend("A", "B")
        with self.assertRaises(ValidationError):
            self._depend("A", "B")

    def test_lag_is_kept_in_business_days(self):
        link = self._depend("A", "B", lag_business_days=3)
        self.assertEqual(link.lag_business_days, 3)

    def test_project_is_derived_from_tasks(self):
        self.assertEqual(self._depend("A", "B").project, self.project)

    def test_dependency_starts_unconfirmed(self):
        self.assertFalse(self._depend("A", "B").is_confirmed)

    def test_confirm_records_reviewer_and_time(self):
        user = User.objects.create_user(
            username="pm",
            email="pm@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        link = self._depend("A", "B").confirm(user)
        self.assertTrue(link.is_confirmed)
        self.assertEqual(link.confirmed_by, user)
        self.assertIsNotNone(link.confirmed_at)


class MilestoneLinkTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        self.task = WbsTask.objects.create(project=self.project, wbs_code="A", name="作業A")
        self.milestone = Milestone.objects.create(
            project=self.project, name="結合試験完了", planned_date=date(2026, 9, 1)
        )

    def test_required_link_defaults_to_true(self):
        link = MilestoneTaskLink.objects.create(milestone=self.milestone, task=self.task)
        self.assertTrue(link.is_required)

    def test_optional_link_can_be_recorded(self):
        link = MilestoneTaskLink.objects.create(
            milestone=self.milestone, task=self.task, is_required=False
        )
        self.assertFalse(link.is_required)

    def test_cross_project_link_is_rejected(self):
        foreign = WbsTask.objects.create(project=self.other, wbs_code="X", name="別案件")
        with self.assertRaises(ValidationError):
            MilestoneTaskLink.objects.create(milestone=self.milestone, task=foreign)

    def test_duplicate_link_is_rejected(self):
        MilestoneTaskLink.objects.create(milestone=self.milestone, task=self.task)
        with self.assertRaises(ValidationError):
            MilestoneTaskLink.objects.create(milestone=self.milestone, task=self.task)


class BusinessCalendarTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.calendar = WorkingCalendar.objects.create(project=self.project)

    def test_missing_calendar_returns_none(self):
        other = Project.objects.create(tenant=self.tenant, code="p9", name="未設定案件")
        self.assertIsNone(BusinessCalendar.for_project(other))

    def test_weekend_is_not_a_working_day(self):
        calendar = BusinessCalendar.for_project(self.project)
        self.assertTrue(calendar.is_working_day(date(2026, 8, 14)))  # 金曜
        self.assertFalse(calendar.is_working_day(date(2026, 8, 15)))  # 土曜

    def test_holiday_is_excluded(self):
        CalendarDay.objects.create(
            calendar=self.calendar, date=date(2026, 8, 14), kind=CalendarDay.Kind.HOLIDAY
        )
        calendar = BusinessCalendar.for_project(self.project)
        self.assertFalse(calendar.is_working_day(date(2026, 8, 14)))

    def test_extra_workday_overrides_holiday(self):
        CalendarDay.objects.create(
            calendar=self.calendar, date=date(2026, 8, 15), kind=CalendarDay.Kind.WORKDAY
        )
        calendar = BusinessCalendar.for_project(self.project)
        self.assertTrue(calendar.is_working_day(date(2026, 8, 15)))

    def test_add_business_days_skips_the_weekend(self):
        calendar = BusinessCalendar.for_project(self.project)
        # 金曜 + 1 営業日 = 翌週月曜。暦日で足すと土曜になる。
        self.assertEqual(calendar.add_business_days(date(2026, 8, 14), 1), date(2026, 8, 17))

    def test_add_business_days_accepts_negative(self):
        calendar = BusinessCalendar.for_project(self.project)
        self.assertEqual(calendar.add_business_days(date(2026, 8, 17), -1), date(2026, 8, 14))

    def test_business_days_between_excludes_the_weekend(self):
        calendar = BusinessCalendar.for_project(self.project)
        self.assertEqual(
            calendar.business_days_between(date(2026, 8, 14), date(2026, 8, 17)), 1
        )

    def test_business_days_between_is_negative_when_earlier(self):
        calendar = BusinessCalendar.for_project(self.project)
        self.assertEqual(
            calendar.business_days_between(date(2026, 8, 17), date(2026, 8, 14)), -1
        )

    def test_freeze_day_is_marked(self):
        CalendarDay.objects.create(
            calendar=self.calendar, date=date(2026, 8, 20), kind=CalendarDay.Kind.FREEZE
        )
        calendar = BusinessCalendar.for_project(self.project)
        self.assertTrue(calendar.is_frozen(date(2026, 8, 20)))
        self.assertTrue(calendar.is_working_day(date(2026, 8, 20)))

    def test_calendar_without_working_weekday_is_rejected(self):
        other = Project.objects.create(tenant=self.tenant, code="p8", name="別案件")
        with self.assertRaises(ValidationError):
            WorkingCalendar.objects.create(project=other, working_weekdays="")

    def test_one_calendar_per_project(self):
        with self.assertRaises(ValidationError):
            WorkingCalendar.objects.create(project=self.project, name="二つ目")
