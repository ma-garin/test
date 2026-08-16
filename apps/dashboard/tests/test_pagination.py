"""一覧のページングと集計の関係を検証する。

先頭 N 件で打ち切っていた頃は「全 300 件」と出ているのに 200 行しか無く、
残りへ到達する手段が無かった。ページングを入れたうえで、集計値まで
ページ単位になっていないことを確認する（ページを送るたびに KPI の数字が
変わると、その数字が何を指すのか読み手に判別できない）。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.pagination import PAGE_SIZE
from apps.projects.models import Project, Risk, WbsTask

#: 2 ページ目が必ず出る件数。
TOTAL_ROWS = 60

#: 期限超過にするタスク数。ページごとに数え直すと必ず値が変わる量にしてある。
OVERDUE_ROWS = 12

#: 主担当のタスク数。絞り込んでも 2 ページに跨るようにしている。
OWNED_ROWS = 55


class DashboardPaginationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-pager",
            email="pmo-pager@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新プロジェクト"
        )
        self.client.force_login(self.user)

    def _create_tasks(self) -> None:
        today = timezone.localdate()

        for index in range(TOTAL_ROWS):
            WbsTask.objects.create(
                project=self.project,
                wbs_code=f"1.{index}",
                name=f"タスク{index}",
                owner="佐藤" if index < OWNED_ROWS else "鈴木",
                status=WbsTask.Status.IN_PROGRESS,
                planned_end=(
                    today - timedelta(days=index + 1)
                    if index < OVERDUE_ROWS
                    else today + timedelta(days=index)
                ),
            )

    def test_task_list_has_second_page(self) -> None:
        """60 件なら 1 ページ目に 50 件、2 ページ目に残り 10 件が出る。"""

        self._create_tasks()

        first = self.client.get(reverse("dashboard:tasks"))
        second = self.client.get(reverse("dashboard:tasks"), {"page": 2})

        self.assertEqual(first.context["page"].paginator.num_pages, 2)
        self.assertEqual(len(first.context["board"].rows), PAGE_SIZE)
        self.assertEqual(len(second.context["board"].rows), TOTAL_ROWS - PAGE_SIZE)

    def test_task_summary_is_stable_across_pages(self) -> None:
        """集計値はページを送っても動かない（全件から数えているため）。"""

        self._create_tasks()

        first = self.client.get(reverse("dashboard:tasks")).context["board"]
        second = self.client.get(reverse("dashboard:tasks"), {"page": 2}).context["board"]

        for board in (first, second):
            self.assertEqual(board.total, TOTAL_ROWS)
            self.assertEqual(board.overdue, OVERDUE_ROWS)
            self.assertEqual(board.in_progress, TOTAL_ROWS)

    def test_task_filter_survives_page_move(self) -> None:
        """絞り込んだまま 2 ページ目へ行っても条件が消えない。"""

        self._create_tasks()

        response = self.client.get(reverse("dashboard:tasks"), {"owner": "佐藤", "page": 2})
        board = response.context["board"]

        self.assertEqual(response.context["page"].paginator.count, OWNED_ROWS)
        self.assertEqual(board.total, OWNED_ROWS)
        self.assertEqual(len(board.rows), OWNED_ROWS - PAGE_SIZE)
        self.assertTrue(all(row.task.owner == "佐藤" for row in board.rows))
        self.assertIn("owner=", response.context["page_query"])
        self.assertContains(response, "page=1")

    def test_risk_list_paginates_without_moving_counts(self) -> None:
        """リスク一覧も同様。高リスク件数はページに依らず一定。"""

        for index in range(TOTAL_ROWS):
            Risk.objects.create(
                project=self.project,
                title=f"リスク{index}",
                probability=5 if index < OVERDUE_ROWS else 2,
                impact=4 if index < OVERDUE_ROWS else 2,
            )

        first = self.client.get(reverse("dashboard:risk"))
        second = self.client.get(reverse("dashboard:risk"), {"page": 2})

        self.assertEqual(len(first.context["report"].rows), PAGE_SIZE)
        self.assertEqual(len(second.context["report"].rows), TOTAL_ROWS - PAGE_SIZE)

        for report in (first.context["report"], second.context["report"]):
            self.assertEqual(report.total, TOTAL_ROWS)
            self.assertEqual(report.high_count, OVERDUE_ROWS)
            self.assertEqual(report.without_mitigation, TOTAL_ROWS)
