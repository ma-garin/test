"""ガントの稲妻線（進捗線）。

PM が進捗会議で最初に見るのはこの線の形である。棒の色は「期限の近さ」しか
表さないため、**期限内でも進捗が足りないタスク**を見つけられない。
稲妻線は本日線からの張り出しでそれを示す。

固定したい性質:

- 進捗 0% の点は棒の左端、100% の点は棒の右端に来る
- 本日より左に出た点は「遅れ」、右は「先行」
- 本日が期間の外なら線を引かない（基準が無いのに形だけ見せない）
- 折れ線は本日線から出て本日線へ戻る（宙に浮かせない）
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.dashboard.services.gantt import build_gantt_chart
from apps.dashboard.services.tasks import TaskFilters, build_task_board
from apps.projects.models import Priority, Project, ProjectMember, WbsTask

TODAY = timezone.localdate()


class ProgressLineTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _task(self, code: str, *, start: int, end: int, progress: int) -> WbsTask:
        return WbsTask.objects.create(
            project=self.project,
            wbs_code=code,
            name=f"タスク{code}",
            priority=Priority.MEDIUM,
            planned_start=TODAY + timedelta(days=start),
            planned_end=TODAY + timedelta(days=end),
            progress_percent=progress,
        )

    def _chart(self, today=None):
        queryset = WbsTask.objects.filter(project=self.project)
        board = build_task_board(queryset, TaskFilters(), list(queryset))

        return build_gantt_chart(board.rows, today or TODAY)

    def test_進捗0の点は棒の左端に来る(self) -> None:
        self._task("1.1", start=-10, end=10, progress=0)

        bar = self._chart().groups[0].bars[0]

        self.assertEqual(bar.progress_left, bar.left)

    def test_進捗100の点は棒の右端に来る(self) -> None:
        self._task("1.1", start=-10, end=10, progress=100)

        bar = self._chart().groups[0].bars[0]

        self.assertAlmostEqual(bar.progress_left, bar.left + bar.width, places=1)

    def test_進捗50の点は棒の中央に来る(self) -> None:
        self._task("1.1", start=-10, end=10, progress=50)

        bar = self._chart().groups[0].bars[0]

        self.assertAlmostEqual(bar.progress_left, bar.left + bar.width / 2, places=1)

    def test_期限内でも進捗不足なら遅れとして左へ出る(self) -> None:
        """棒の色（期限の近さ）では捕まえられないケース。"""

        # 20日間のうち15日経過しているのに、進捗は10%しかない。
        self._task("1.1", start=-15, end=5, progress=10)

        line = self._chart().groups[0].progress_line

        self.assertTrue(line.points[0].is_behind)
        self.assertEqual(line.behind_count, 1)

    def test_前倒しなら右へ出る(self) -> None:
        self._task("1.1", start=-5, end=15, progress=90)

        line = self._chart().groups[0].progress_line

        self.assertTrue(line.points[0].is_ahead)
        self.assertEqual(line.ahead_count, 1)

    def test_最も遅れているタスクを特定できる(self) -> None:
        self._task("1.1", start=-15, end=5, progress=80)
        self._task("1.2", start=-15, end=5, progress=5)

        line = self._chart().groups[0].progress_line

        self.assertEqual(line.worst.bar.task.wbs_code, "1.2")

    def test_遅れが無ければ最悪のタスクは無い(self) -> None:
        self._task("1.1", start=-5, end=15, progress=95)

        line = self._chart().groups[0].progress_line

        self.assertIsNone(line.worst)

    def test_本日が期間外なら線を引かない(self) -> None:
        """基準が図の外にあるとき、形だけ見せると誤読される。"""

        self._task("1.1", start=10, end=20, progress=0)

        chart = self._chart()

        self.assertIsNone(chart.today_left)
        self.assertIsNone(chart.groups[0].progress_line)
        self.assertFalse(chart.has_progress_line)

    def test_折れ線は本日線から出て本日線へ戻る(self) -> None:
        self._task("1.1", start=-10, end=10, progress=0)
        self._task("1.2", start=-10, end=10, progress=100)

        line = self._chart().groups[0].progress_line
        points = line.polyline.split()

        self.assertEqual(points[0], f"{line.today_left},0")
        self.assertEqual(points[-1], f"{line.today_left},2")
        self.assertEqual(len(points), 4)

    def test_案件ごとに線を分ける(self) -> None:
        """案件見出しの高さを挟むと座標がずれるため、行だけの範囲ごとに引く。"""

        other = Project.objects.create(tenant=self.tenant, code="p2", name="別案件")
        self._task("1.1", start=-10, end=10, progress=30)
        WbsTask.objects.create(
            project=other,
            wbs_code="2.1",
            name="別案件のタスク",
            priority=Priority.MEDIUM,
            planned_start=TODAY - timedelta(days=10),
            planned_end=TODAY + timedelta(days=10),
            progress_percent=60,
        )

        queryset = WbsTask.objects.all()
        board = build_task_board(queryset, TaskFilters(), list(queryset))
        chart = build_gantt_chart(board.rows, TODAY)

        self.assertEqual(len(chart.groups), 2)

        for group in chart.groups:
            self.assertEqual(group.progress_line.row_count, len(group.bars))


class ProgressLineViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = str(self.tenant.pk)
        session.save()

        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.PMO
        )

    def test_ガント画面に稲妻線が描かれる(self) -> None:
        WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="結合試験",
            priority=Priority.MEDIUM,
            planned_start=TODAY - timedelta(days=10),
            planned_end=TODAY + timedelta(days=10),
            progress_percent=20,
        )

        response = self.client.get(reverse("dashboard:tasks"), {"view": "gantt"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gantt-line")
        self.assertContains(response, "<polyline")
        self.assertContains(response, "稲妻線")

    def test_凡例に遅れと先行の件数が出る(self) -> None:
        WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="遅れているタスク",
            priority=Priority.MEDIUM,
            planned_start=TODAY - timedelta(days=15),
            planned_end=TODAY + timedelta(days=5),
            progress_percent=5,
        )

        response = self.client.get(reverse("dashboard:tasks"), {"view": "gantt"})

        self.assertContains(response, "遅れ 1件")
        self.assertContains(response, "最も遅れているのは 1.1 遅れているタスク")
