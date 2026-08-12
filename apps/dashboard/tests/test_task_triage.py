"""JT-01〜JT-05: タスク一覧・詳細を「今日処理する仕事の入口」にする変更のテスト。

見た目ではなく外部挙動を固定する。すなわち「1 クリックで危険な対象へ絞り込める」
「行の詳細導線が 1 つである」「危険行が強調される」「処理すべき対象が表より先に出る」
「詳細を開いた直後に次の一手と不足情報が分かる」を確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.services.tasks import TaskFilters, build_task_board
from apps.projects.models import Priority, Project, WbsTask


class TaskTriageTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-user",
            email="pmo-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新プロジェクト"
        )
        self.client.force_login(self.user)
        today = timezone.localdate()

        self.blocked = WbsTask.objects.create(
            project=self.project,
            wbs_code="W-10",
            name="結合試験の再開",
            status=WbsTask.Status.BLOCKED,
            priority=Priority.URGENT,
            owner="山田",
            ball_holder="佐藤",
            planned_end=today + timedelta(days=3),
        )
        self.overdue = WbsTask.objects.create(
            project=self.project,
            wbs_code="W-11",
            name="受入テスト仕様の確定",
            status=WbsTask.Status.IN_PROGRESS,
            owner="鈴木",
            planned_end=today - timedelta(days=2),
        )
        self.done = WbsTask.objects.create(
            project=self.project,
            wbs_code="W-01",
            name="要件定義",
            status=WbsTask.Status.DONE,
            planned_end=today - timedelta(days=30),
        )

    # ── JT-01 ────────────────────────────────────────────────
    def test_quick_views_use_existing_get_conditions(self):
        response = self.client.get(reverse("dashboard:tasks"))
        content = response.content.decode()
        for query in ("status=blocked", "due=overdue", "due=due_soon", "status=not_started"):
            self.assertIn(query, content, query)

    def test_quick_view_filters_to_blocked_only(self):
        response = self.client.get(reverse("dashboard:tasks"), {"status": "blocked"})
        rows = response.context["board"].rows
        self.assertEqual([r.task.pk for r in rows], [self.blocked.pk])

    def test_active_quick_view_is_marked(self):
        response = self.client.get(reverse("dashboard:tasks"), {"status": "blocked"})
        active = [q.key for q in response.context["board"].quick_views if q.is_active]
        self.assertEqual(active, ["blocked"])

    def test_clearing_conditions_returns_all_tasks(self):
        response = self.client.get(reverse("dashboard:tasks"))
        self.assertEqual(len(response.context["board"].rows), 3)
        self.assertFalse(any(q.is_active for q in response.context["board"].quick_views))

    # ── JT-02 ────────────────────────────────────────────────
    def test_row_has_single_detail_link(self):
        """行の詳細導線はタスク名だけにする。『今すぐ確認』の導線は別区画なので数えない。"""
        response = self.client.get(reverse("dashboard:tasks"))
        detail_url = reverse("projects:task_detail", args=[self.blocked.pk])
        table = self._task_table(response.content.decode())
        self.assertEqual(table.count(f'href="{detail_url}?next='), 1)

    def test_detail_button_is_removed_from_rows(self):
        response = self.client.get(reverse("dashboard:tasks"))
        self.assertNotIn(">詳細</a>", self._task_table(response.content.decode()))

    @staticmethod
    def _task_table(content: str) -> str:
        """タスク一覧の表だけを取り出す。"""
        start = content.index("task-list-table")
        return content[start : content.index("</table>", start)]

    def test_edit_link_remains(self):
        response = self.client.get(reverse("dashboard:tasks"))
        edit_url = reverse("projects:task_edit", args=[self.blocked.pk])
        self.assertIn(edit_url, response.content.decode())

    # ── JT-03 ────────────────────────────────────────────────
    def test_row_tone_marks_danger_rows_only(self):
        board = build_task_board(
            WbsTask.objects.filter(project=self.project).order_by("wbs_code"), TaskFilters()
        )
        tones = {row.task.wbs_code: row.row_tone for row in board.rows}
        self.assertEqual(tones["W-10"], "row-blocked")
        self.assertEqual(tones["W-11"], "row-overdue")
        self.assertEqual(tones["W-01"], "")

    def test_row_tone_is_rendered(self):
        response = self.client.get(reverse("dashboard:tasks"))
        self.assertIn('class="row-blocked"', response.content.decode())

    # ── JT-04 ────────────────────────────────────────────────
    def test_attention_puts_blocked_first_with_reason(self):
        board = build_task_board(WbsTask.objects.filter(project=self.project), TaskFilters())
        self.assertEqual(board.attention[0].task.pk, self.blocked.pk)
        self.assertIn("ブロック中", board.attention[0].reason)
        self.assertIn("佐藤", board.attention[0].reason)

    def test_attention_reports_overdue_days(self):
        board = build_task_board(WbsTask.objects.filter(project=self.project), TaskFilters())
        overdue = [i for i in board.attention if i.task.pk == self.overdue.pk]
        self.assertEqual(overdue[0].reason, "期限を 2 日超過")

    def test_attention_excludes_completed_tasks(self):
        board = build_task_board(WbsTask.objects.filter(project=self.project), TaskFilters())
        self.assertNotIn(self.done.pk, [i.task.pk for i in board.attention])

    def test_attention_is_capped_at_three(self):
        today = timezone.localdate()
        for index in range(5):
            WbsTask.objects.create(
                project=self.project,
                wbs_code=f"W-2{index}",
                name=f"追加タスク{index}",
                status=WbsTask.Status.BLOCKED,
                planned_end=today,
            )
        board = build_task_board(WbsTask.objects.filter(project=self.project), TaskFilters())
        self.assertEqual(len(board.attention), 3)

    def test_attention_is_computed_from_all_pages(self):
        """ページ送りしても「今すぐ確認」が消えないこと。"""
        board = build_task_board(
            WbsTask.objects.filter(project=self.project),
            TaskFilters(),
            display_tasks=[self.done],
        )
        self.assertEqual([r.task.pk for r in board.rows], [self.done.pk])
        self.assertEqual(board.attention[0].task.pk, self.blocked.pk)

    def test_empty_attention_shows_safe_state(self):
        WbsTask.objects.exclude(pk=self.done.pk).delete()
        response = self.client.get(reverse("dashboard:tasks"))
        self.assertEqual(response.context["board"].attention, ())
        self.assertContains(response, "ブロック中・期限超過・7日以内のタスクはありません")

    # ── JT-05 ────────────────────────────────────────────────
    def test_detail_shows_next_action_card(self):
        response = self.client.get(reverse("projects:task_detail", args=[self.blocked.pk]))
        self.assertContains(response, "次アクション")
        self.assertContains(response, "佐藤")

    def test_detail_marks_missing_next_action_and_evidence(self):
        response = self.client.get(reverse("projects:task_detail", args=[self.blocked.pk]))
        content = response.content.decode()
        self.assertIn("次アクションが空です", content)
        self.assertIn("根拠メモがありません", content)

    def test_detail_keeps_attributes_list(self):
        """要約カードを足しても、既存の属性一覧は残す。"""
        response = self.client.get(reverse("projects:task_detail", args=[self.blocked.pk]))
        self.assertContains(response, "WBSコード")
        self.assertContains(response, "クリティカルパス")
