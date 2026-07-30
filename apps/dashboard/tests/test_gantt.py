"""タスク一覧のガント表示テスト。

ガントは「見た目が出た」ことより「表と同じ対象が、正しい位置に、漏れなく」
出ていることが重要なので、位置計算の範囲・期間未設定の別枠・絞り込みの反映・
0 件時の挙動を確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.services.gantt import build_gantt_chart
from apps.dashboard.services.tasks import TaskFilters, build_task_board
from apps.projects.models import Project, WbsTask


class GanttTestBase(TestCase):
    """データ準備。位置計算と画面表示の両方で同じ前提を使う。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-gantt",
            email="pmo-gantt@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新プロジェクト"
        )
        self.today = timezone.localdate()
        self.client.force_login(self.user)

    def _task(self, **kwargs) -> WbsTask:
        defaults = {
            "project": self.project,
            "wbs_code": "1.1",
            "name": "要件定義レビュー",
            "progress_percent": 40,
        }

        return WbsTask.objects.create(**{**defaults, **kwargs})

    def _chart(self):
        board = build_task_board(WbsTask.objects.filter(project=self.project), TaskFilters())

        return build_gantt_chart(board.rows, self.today)


class GanttChartTests(GanttTestBase):
    """位置計算。"""

    def test_positions_stay_within_0_to_100(self) -> None:
        self._task(
            wbs_code="1.1",
            planned_start=self.today - timedelta(days=10),
            planned_end=self.today - timedelta(days=3),
        )
        self._task(
            wbs_code="1.2",
            name="結合テスト",
            planned_start=self.today,
            planned_end=self.today + timedelta(days=20),
        )

        chart = self._chart()
        bars = [bar for group in chart.groups for bar in group.bars]

        self.assertEqual(len(bars), 2)
        for bar in bars:
            self.assertGreaterEqual(bar.left, 0)
            self.assertGreater(bar.width, 0)
            self.assertLessEqual(bar.left + bar.width, 100.0)
            self.assertLessEqual(bar.progress_width, 100.0)

    def test_progress_is_painted_inside_the_bar(self) -> None:
        self._task(
            planned_start=self.today,
            planned_end=self.today + timedelta(days=4),
            progress_percent=65,
        )

        bar = self._chart().groups[0].bars[0]

        self.assertEqual(bar.progress_width, 65.0)

    def test_single_day_span_does_not_divide_by_zero(self) -> None:
        self._task(planned_start=self.today, planned_end=self.today)
        self._task(wbs_code="1.2", name="同日タスク", planned_start=self.today, planned_end=self.today)

        chart = self._chart()
        bars = [bar for group in chart.groups for bar in group.bars]

        self.assertEqual(chart.days, 1)
        self.assertEqual([bar.width for bar in bars], [100.0, 100.0])
        self.assertEqual(chart.today_left, 0.0)

    def test_tasks_without_dates_go_to_a_separate_list(self) -> None:
        self._task(planned_start=self.today, planned_end=self.today + timedelta(days=2))
        self._task(wbs_code="2.1", name="期間なしタスク", planned_start=None, planned_end=None)
        self._task(wbs_code="2.2", name="開始日なしタスク", planned_end=self.today)

        chart = self._chart()

        self.assertEqual(chart.bar_count, 1)
        self.assertEqual(
            sorted(row.task.wbs_code for row in chart.undated), ["2.1", "2.2"]
        )

    def test_chart_is_empty_when_there_are_no_rows(self) -> None:
        chart = build_gantt_chart((), self.today)

        self.assertTrue(chart.is_empty)
        self.assertEqual(chart.groups, ())
        self.assertIsNone(chart.today_left)

    def test_today_line_is_hidden_outside_the_span(self) -> None:
        self._task(
            planned_start=self.today + timedelta(days=30),
            planned_end=self.today + timedelta(days=40),
        )

        self.assertFalse(self._chart().has_today)

    def test_groups_are_split_per_project(self) -> None:
        other = Project.objects.create(tenant=self.tenant, code="p2", name="別案件")
        self._task(planned_start=self.today, planned_end=self.today + timedelta(days=1))
        self._task(
            project=other,
            wbs_code="1.1",
            name="別案件のタスク",
            planned_start=self.today,
            planned_end=self.today + timedelta(days=1),
        )

        board = build_task_board(WbsTask.objects.all(), TaskFilters())
        chart = build_gantt_chart(board.rows, self.today)

        self.assertEqual(sorted(group.code for group in chart.groups), ["p1", "p2"])


class GanttViewTests(GanttTestBase):
    """画面側。絞り込みが表とガントで同じに効くことを確認する。"""

    def test_gantt_view_renders_bars(self) -> None:
        self._task(planned_start=self.today, planned_end=self.today + timedelta(days=3))

        response = self.client.get(reverse("dashboard:tasks"), {"view": "gantt"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "要件定義レビュー")
        self.assertEqual(response.context["chart"].bar_count, 1)

    def test_gantt_view_lists_undated_tasks(self) -> None:
        self._task(wbs_code="9.9", name="期間なしタスク")

        response = self.client.get(reverse("dashboard:tasks"), {"view": "gantt"})

        self.assertContains(response, "期間未設定")
        self.assertContains(response, "期間なしタスク")

    def test_filters_apply_to_the_gantt_view(self) -> None:
        self._task(
            wbs_code="1.1",
            name="完了タスク",
            status=WbsTask.Status.DONE,
            planned_start=self.today,
            planned_end=self.today + timedelta(days=1),
        )
        self._task(
            wbs_code="1.2",
            name="未着手タスク",
            status=WbsTask.Status.NOT_STARTED,
            planned_start=self.today,
            planned_end=self.today + timedelta(days=1),
        )

        response = self.client.get(reverse("dashboard:tasks"), {"view": "gantt", "status": "done"})

        self.assertContains(response, "完了タスク")
        self.assertNotContains(response, "未着手タスク")

    def test_gantt_view_without_tasks_is_not_an_error(self) -> None:
        response = self.client.get(reverse("dashboard:tasks"), {"view": "gantt"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "参照できるタスクがありません")
        self.assertFalse(response.context["chart"].has_bars)

    def test_table_view_is_still_the_default(self) -> None:
        self._task(planned_start=self.today, planned_end=self.today + timedelta(days=1))

        response = self.client.get(reverse("dashboard:tasks"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/task_list.html")
