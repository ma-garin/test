"""業務画面の状態・安全性・アクセシビリティの回帰テスト。"""

from __future__ import annotations

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.core import views as core_views
from apps.projects.models import ChangeRequest, Defect, Issue, Project, ProjectMember, Risk, WbsTask


class UiQualityTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="quality", name="品質確認テナント")
        self.user = User.objects.create_user(
            username="quality-user",
            email="quality-user@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.project = Project.objects.create(
            tenant=self.tenant,
            code="quality-project",
            name="品質確認案件",
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectRole.OWNER,
        )
        self.task = WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="品質確認タスク",
        )
        self.client.force_login(self.user)

    def test_画面は現在地と表示範囲を支援技術へ伝える(self):
        response = self.client.get(reverse("dashboard:tasks"))

        self.assertContains(response, 'aria-labelledby="page-heading"')
        self.assertContains(response, 'id="page-heading"')
        self.assertContains(response, "表示範囲")
        self.assertContains(response, "品質確認テナント")
        self.assertContains(response, 'aria-current="page"')

    def test_画面切替は既存画面を保つ拡張遷移を利用できる(self):
        response = self.client.get(reverse("dashboard:tasks"))

        self.assertContains(response, 'id="page-navigation-status"')
        self.assertContains(response, "X-VeriRAG-Navigation")
        self.assertContains(response, "window.VeriRagUi.visit = visit")

    def test_一覧から詳細へ進んでも探索条件を持ち帰れる(self):
        response = self.client.get(
            reverse("dashboard:tasks"),
            {"owner": "担当A", "status": "in_progress", "per_page": 20},
        )

        expected_next = "/tasks/?owner=%E6%8B%85%E5%BD%93A&amp;status=in_progress&amp;per_page=20"
        # URL をネストして渡すため、元の % は 1 回だけエスケープされる。
        self.assertContains(response, "next=/tasks/%3Fowner%3D%25E6%258B%2585")

        detail = self.client.get(
            reverse("projects:task_detail", args=[self.task.pk]),
            {"next": "/tasks/?owner=%E6%8B%85%E5%BD%93A&status=in_progress&per_page=20"},
        )

        self.assertContains(detail, expected_next)

    def test_高影響操作は確認メッセージを持つ(self):
        response = self.client.get(reverse("projects:task_edit", args=[self.task.pk]))

        self.assertContains(response, 'data-confirm="このタスクをアーカイブします。')
        self.assertContains(response, "一覧の既定表示から外れます")

    def test_一覧の戻り先は保持し外部URLは採用しない(self):
        return_to = "/tasks/?status=in_progress&per_page=20"
        response = self.client.post(
            reverse("projects:task_archive", args=[self.task.pk]),
            {"next": return_to},
        )
        self.assertRedirects(response, return_to)

        response = self.client.get(
            reverse("projects:task_edit", args=[self.task.pk]),
            {"next": "https://example.invalid/"},
        )
        self.assertContains(response, 'href="/tasks/"')

    def test_主要台帳も戻り先の方針を共有する(self):
        change = ChangeRequest.objects.create(project=self.project, title="品質確認変更")
        risk = Risk.objects.create(project=self.project, title="品質確認リスク")
        issue = Issue.objects.create(project=self.project, title="品質確認課題")
        defect = Defect.objects.create(project=self.project, title="品質確認不具合")

        checks = (
            ("projects:change_edit", change.pk, "/change/?status=pending&page=2"),
            ("projects:risk_edit", risk.pk, "/risk/?status=open&page=2"),
            ("projects:issue_edit", issue.pk, "/projects/issues/?page=2"),
            ("projects:defect_edit", defect.pk, "/projects/defects/?page=2"),
        )
        for url_name, pk, return_to in checks:
            response = self.client.get(reverse(url_name, args=[pk]), {"next": return_to})
            self.assertContains(response, f'href="{return_to.replace("&", "&amp;")}"')

    @override_settings(DEBUG=False)
    def test_存在しない画面は次の行動を示す404画面になる(self):
        response = self.client.get("/not-found/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "ページが見つかりません", status_code=404)
        self.assertContains(response, "ログイン画面へ", status_code=404)

    def test_例外画面は原因と次の行動を示す(self):
        request = RequestFactory().get("/test-error/")

        for response, expected_status, heading in (
            (core_views.bad_request(request), 400, "リクエストを確認してください"),
            (core_views.permission_denied(request), 403, "この操作は許可されていません"),
            (core_views.server_error(request), 500, "一時的に処理できません"),
        ):
            self.assertEqual(response.status_code, expected_status)
            self.assertContains(response, heading, status_code=expected_status)
            self.assertContains(response, "ログイン画面へ", status_code=expected_status)
