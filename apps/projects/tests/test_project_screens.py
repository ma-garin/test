"""UXP-30 / UXP-31 / UXP-38: 案件一覧・案件詳細・タスク詳細を次の操作の入口にする変更のテスト。

見た目ではなく外部挙動を固定する。すなわち「一覧から詳細へ入れる」「一覧に状態・
更新時点・未解決件数が出る」「詳細の先頭に次に対応すべきことが 1 件だけ出る」
「各台帳の全件へ抜けられる」「タスク詳細で関連・子タスクの名前から詳細へ入れる」
「JT-05 の次アクションカードと属性一覧が両方残る」を確認する。
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.projects.models import ChangeRequest, Issue, Project, WbsTask

#: 「全件を見る」と書かれたリンクの遷移先だけを取り出す。
#: ナビゲーションにも同じ URL が出るため、単純な文字列一致では検証にならない。
SEE_ALL_LINK = re.compile(r'<a href="([^"]+)">全件を見る')


class ProjectScreenTestCase(TestCase):
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
        self.today = timezone.localdate()


class ProjectListTests(ProjectScreenTestCase):
    def setUp(self) -> None:
        super().setUp()
        Issue.objects.create(project=self.project, title="残課題A", status=Issue.Status.OPEN)
        Issue.objects.create(
            project=self.project, title="残課題B", status=Issue.Status.IN_PROGRESS
        )
        Issue.objects.create(
            project=self.project, title="片付いた課題", status=Issue.Status.CLOSED
        )

    def test_project_name_links_to_detail(self):
        """UXP-30: 案件名から詳細へ入れる。"""
        response = self.client.get(reverse("projects:list"))
        detail_url = reverse("projects:detail", args=[self.project.pk])
        self.assertContains(response, f'<a href="{detail_url}">基幹刷新プロジェクト</a>', html=False)

    def test_list_shows_status_updated_at_and_open_issue_columns(self):
        """UXP-30: 状態・更新時点・未解決件数を最小列として出す。"""
        response = self.client.get(reverse("projects:list"))
        content = response.content.decode()
        for header in ("<th>状態</th>", "<th>更新時点</th>", "<th>未解決課題</th>"):
            self.assertIn(header, content, header)
        self.assertIn(self.project.updated_at.strftime("%Y"), content)

    def test_open_issue_count_excludes_resolved_issues(self):
        """UXP-30: 未解決件数は推測せず、未対応・対応中・ブロック中だけを数える。"""
        response = self.client.get(reverse("projects:list"))
        rows = {row.code: row for row in response.context["projects"]}
        self.assertEqual(rows["p1"].open_issue_count, 2)

    def test_open_issue_count_is_zero_without_issues(self):
        Issue.objects.all().delete()
        response = self.client.get(reverse("projects:list"))
        self.assertEqual(response.context["projects"][0].open_issue_count, 0)


class ProjectDetailNextActionTests(ProjectScreenTestCase):
    def _detail(self):
        return self.client.get(reverse("projects:detail", args=[self.project.pk]))

    def test_overdue_task_is_shown_as_the_single_next_action(self):
        """UXP-31: 期限超過が最優先で、1 件だけ出る。"""
        WbsTask.objects.create(
            project=self.project,
            wbs_code="W-11",
            name="受入テスト仕様の確定",
            status=WbsTask.Status.IN_PROGRESS,
            owner="鈴木",
            planned_end=self.today - timedelta(days=2),
        )
        WbsTask.objects.create(
            project=self.project,
            wbs_code="W-10",
            name="結合試験の再開",
            status=WbsTask.Status.BLOCKED,
            planned_end=self.today + timedelta(days=3),
        )
        response = self._detail()
        action = response.context["next_action"]
        self.assertEqual(action["reason"], "期限超過")
        self.assertEqual(action["title"], "受入テスト仕様の確定")
        self.assertEqual(action["url"], reverse("projects:task_detail", args=[
            WbsTask.objects.get(wbs_code="W-11").pk
        ]))
        self.assertContains(response, "受入テスト仕様の確定")

    def test_blocked_task_is_used_when_nothing_is_overdue(self):
        """UXP-31: 期限超過が無ければブロック中を出す。"""
        WbsTask.objects.create(
            project=self.project,
            wbs_code="W-10",
            name="結合試験の再開",
            status=WbsTask.Status.BLOCKED,
            planned_end=self.today + timedelta(days=3),
        )
        action = self._detail().context["next_action"]
        self.assertEqual(action["reason"], "ブロック中")
        self.assertEqual(action["title"], "結合試験の再開")

    def test_pending_change_request_is_used_as_the_last_resort(self):
        """UXP-31: 期限超過もブロック中も無ければ判断待ちを出す。"""
        ChangeRequest.objects.create(
            project=self.project,
            title="スコープ追加の承認",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )
        response = self._detail()
        action = response.context["next_action"]
        self.assertEqual(action["reason"], "判断待ち")
        self.assertEqual(action["url"], reverse("dashboard:change"))
        self.assertContains(response, "スコープ追加の承認")

    def test_completed_task_never_becomes_the_next_action(self):
        """完了済みの遅れを次の一手にすると、本当に動くべき対象が埋もれる。"""
        WbsTask.objects.create(
            project=self.project,
            wbs_code="W-01",
            name="要件定義",
            status=WbsTask.Status.DONE,
            planned_end=self.today - timedelta(days=30),
        )
        response = self._detail()
        self.assertIsNone(response.context["next_action"])
        self.assertContains(response, "期限超過・ブロック中・判断待ちはありません")

    def test_overdue_issue_links_to_the_filtered_issue_list(self):
        """課題は詳細画面を持たないので、絞り込み済みの一覧へ送る。"""
        Issue.objects.create(
            project=self.project,
            title="外部連携の仕様未確定",
            status=Issue.Status.OPEN,
            due_date=self.today - timedelta(days=5),
        )
        action = self._detail().context["next_action"]
        self.assertEqual(action["title"], "外部連携の仕様未確定")
        self.assertEqual(action["url"], f"{reverse('projects:issue_list')}?due=overdue")


class ProjectDetailSeeAllTests(ProjectScreenTestCase):
    def test_each_ledger_section_has_a_see_all_link(self):
        """UXP-31: 課題・リスク・タスク・変更から、それぞれの台帳へ抜けられる。"""
        response = self.client.get(reverse("projects:detail", args=[self.project.pk]))
        targets = set(SEE_ALL_LINK.findall(response.content.decode()))
        self.assertEqual(
            targets,
            {
                reverse("projects:issue_list"),
                reverse("dashboard:risk"),
                reverse("dashboard:tasks"),
                reverse("dashboard:change"),
                reverse("projects:defect_list"),
            },
        )


class TaskDetailTests(ProjectScreenTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = WbsTask.objects.create(
            project=self.project,
            wbs_code="W-10",
            name="結合試験の再開",
            status=WbsTask.Status.BLOCKED,
            owner="山田",
            ball_holder="佐藤",
            planned_end=self.today + timedelta(days=3),
        )
        self.child = WbsTask.objects.create(
            project=self.project, wbs_code="W-10-1", name="試験環境の復旧", parent=self.task
        )
        self.related = WbsTask.objects.create(
            project=self.project, wbs_code="W-12", name="移行リハーサル"
        )
        self.task.related_tasks.add(self.related)

    def _related_table(self, content: str) -> str:
        start = content.index("関連タスク")
        return content[start : content.index("</table>", start)]

    def test_related_and_child_task_names_link_to_detail(self):
        """UXP-38: 関連・子タスクの名前から詳細へ入れる。"""
        response = self.client.get(reverse("projects:task_detail", args=[self.task.pk]))
        table = self._related_table(response.content.decode())
        for task in (self.related, self.child):
            url = reverse("projects:task_detail", args=[task.pk])
            self.assertIn(f'<a href="{url}">{task.name}</a>', table, task.name)

    def test_head_collects_status_owner_due_and_next_action(self):
        """UXP-38: 属性一覧を読む前に、状態・担当・期限・次アクションが揃っている。"""
        content = self.client.get(
            reverse("projects:task_detail", args=[self.task.pk])
        ).content.decode()
        head = content[: content.index("タスク情報")]
        for label in ("状態", "担当", "期限", "次にすること"):
            self.assertIn(f"<dt>{label}</dt>", head, label)

    def test_next_action_card_and_attribute_list_both_remain(self):
        """JT-05 のカードを消さない。属性一覧も残す。"""
        response = self.client.get(reverse("projects:task_detail", args=[self.task.pk]))
        self.assertContains(response, "次アクション")
        self.assertContains(response, "佐藤")
        self.assertContains(response, "WBSコード")
        self.assertContains(response, "クリティカルパス")

    def test_related_section_reports_both_counts(self):
        response = self.client.get(reverse("projects:task_detail", args=[self.task.pk]))
        self.assertContains(response, "関連 1件 ／ 子タスク 1件")
