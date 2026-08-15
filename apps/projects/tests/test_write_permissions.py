"""案件配下の書き込み・判断に対する権限テスト。

既存のビューのテストはテナント管理者と PMO ばかりで、権限の切れ目を通って
いなかった。ここでは *参照しかできない立場* から書き込みを直接 POST し、

1. 403 が返ること
2. レコードが 1 件も増減せず、状態も変わらないこと

の 2 点を必ず対で確かめる。403 だけを見ると、画面が拒否を返しながら裏で
保存している実装を通してしまう。

併せて「締めすぎていないこと」（案件メンバーは編集できる）と、
「案件内の役割がテナントロールより優先されること」も検証する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Issue,
    Project,
    ProjectMember,
    Risk,
    WbsTask,
)


class ProjectWritePermissionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

        # 参照のみ。テナントロールも案件役割も「参照」。
        self.viewer = self._member("viewer", Role.VIEWER, ProjectRole.VIEWER)
        # 編集はできるが承認はできない立場（締めすぎの検出用）。
        self.member = self._member("member", Role.CHANGE_MANAGER, ProjectRole.MEMBER)
        # テナントロールは編集・承認できるが、案件では参照しかできない立場。
        # 案件役割が優先されることの検証に使う。
        self.tenant_pmo = self._member("tenant-pmo", Role.PMO, ProjectRole.VIEWER)

        self.task = WbsTask.objects.create(project=self.project, wbs_code="1.1", name="要件定義")
        self.issue = Issue.objects.create(project=self.project, title="課題1")
        self.risk = Risk.objects.create(project=self.project, title="リスク1")
        self.defect = Defect.objects.create(project=self.project, title="不具合1")
        self.change = ChangeRequest.objects.create(
            project=self.project,
            title="変更要求1",
            status=ChangeRequest.Status.PENDING_APPROVAL,
        )

    def _member(self, name: str, role: str, project_role: str) -> User:
        user = User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=role,
        )
        ProjectMember.objects.create(project=self.project, user=user, role=project_role)

        return user

    # --- 送信内容 -----------------------------------------------------------

    def _task_payload(self) -> dict:
        return {
            "project": str(self.project.pk),
            "wbs_code": "9.9",
            "name": "権限テストで作成したタスク",
            "owner": "山田",
            "planned_end": "2026-12-31",
            "progress_percent": "0",
            "priority": "medium",
            "status": "not_started",
            "follow_up_state": "none",
        }

    def _issue_payload(self) -> dict:
        return {
            "project": str(self.project.pk),
            "title": "権限テストで起票した課題",
            "status": Issue.Status.OPEN,
            "severity": "medium",
        }

    def _risk_payload(self) -> dict:
        return {
            "project": str(self.project.pk),
            "title": "権限テストで登録したリスク",
            "status": Risk.Status.IDENTIFIED,
            "impact": "3",
            "probability": "3",
        }

    def _defect_payload(self) -> dict:
        return {
            "project": str(self.project.pk),
            "title": "権限テストで登録した不具合",
            "status": Defect.Status.NEW,
            "severity": "medium",
        }

    def _change_payload(self) -> dict:
        return {
            "project": str(self.project.pk),
            "title": "権限テストで起票した変更要求",
            "status": ChangeRequest.Status.DRAFT,
        }

    def _assert_denied_without_change(self, model, url: str, payload: dict | None = None) -> None:
        """403 が返り、かつ件数が動かないことを対で確かめる。"""

        before = model.objects.count()
        response = self.client.post(url, payload or {})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(model.objects.count(), before)

    # --- 参照専用ロール -----------------------------------------------------

    def test_参照専用ロールは新規作成できずレコードも増えない(self):
        self.client.force_login(self.viewer)

        cases = (
            (WbsTask, "projects:task_create", self._task_payload()),
            (Issue, "projects:issue_create", self._issue_payload()),
            (Risk, "projects:risk_create", self._risk_payload()),
            (Defect, "projects:defect_create", self._defect_payload()),
            (ChangeRequest, "projects:change_create", self._change_payload()),
        )

        for model, url_name, payload in cases:
            with self.subTest(url=url_name):
                self._assert_denied_without_change(model, reverse(url_name), payload)

    def test_参照専用ロールには編集フォームも見せない(self):
        """保存できないフォームを開かせない。押してから断るのは権限の漏れ。"""

        self.client.force_login(self.viewer)

        cases = (
            ("projects:task_edit", self.task.pk),
            ("projects:issue_edit", self.issue.pk),
            ("projects:risk_edit", self.risk.pk),
            ("projects:risk_promote", self.risk.pk),
            ("projects:defect_edit", self.defect.pk),
            ("projects:change_edit", self.change.pk),
        )

        for url_name, pk in cases:
            with self.subTest(url=url_name):
                response = self.client.get(reverse(url_name, args=[pk]))

                self.assertEqual(response.status_code, 403)

    def test_参照専用ロールは既存レコードを更新できない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("projects:task_edit", args=[self.task.pk]),
            {**self._task_payload(), "name": "書き換えたタスク名"},
        )

        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.name, "要件定義")

    def test_参照専用ロールはクローズもアーカイブもできない(self):
        self.client.force_login(self.viewer)

        cases = (
            ("projects:task_archive", self.task),
            ("projects:issue_close", self.issue),
            ("projects:risk_close", self.risk),
            ("projects:defect_close", self.defect),
        )

        for url_name, obj in cases:
            with self.subTest(url=url_name):
                response = self.client.post(reverse(url_name, args=[obj.pk]))

                self.assertEqual(response.status_code, 403)

        self.task.refresh_from_db()
        self.issue.refresh_from_db()
        self.risk.refresh_from_db()
        self.defect.refresh_from_db()

        self.assertFalse(self.task.is_archived)
        self.assertEqual(self.issue.status, Issue.Status.OPEN)
        self.assertEqual(self.risk.status, Risk.Status.IDENTIFIED)
        self.assertEqual(self.defect.status, Defect.Status.NEW)

    def test_参照専用ロールはリスクを課題へ転換できない(self):
        self.client.force_login(self.viewer)

        before = Issue.objects.count()
        response = self.client.post(
            reverse("projects:risk_promote", args=[self.risk.pk]),
            {"title": "転換された課題", "status": Issue.Status.OPEN, "severity": "medium"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Issue.objects.count(), before)

    def test_参照専用ロールは変更要求を判断できない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("projects:change_decide", args=[self.change.pk]),
            {"decision": "approved", "reason": "権限がないのに承認"},
        )

        self.assertEqual(response.status_code, 403)
        self.change.refresh_from_db()
        self.assertEqual(self.change.status, ChangeRequest.Status.PENDING_APPROVAL)
        self.assertIsNone(self.change.decided_by)

    # --- 案件役割が優先されること -------------------------------------------

    def test_テナントロールが編集可でも案件で参照のみなら書けない(self):
        """案件内の役割はテナントロールより優先する。"""

        self.client.force_login(self.tenant_pmo)

        self._assert_denied_without_change(
            WbsTask, reverse("projects:task_create"), self._task_payload()
        )

    def test_テナントロールが承認可でも案件で参照のみなら判断できない(self):
        self.client.force_login(self.tenant_pmo)

        response = self.client.post(
            reverse("projects:change_decide", args=[self.change.pk]),
            {"decision": "approved", "reason": "案件では参照しかできない"},
        )

        self.assertEqual(response.status_code, 403)
        self.change.refresh_from_db()
        self.assertEqual(self.change.status, ChangeRequest.Status.PENDING_APPROVAL)

    # --- 締めすぎていないこと -----------------------------------------------

    def test_案件メンバーは作成もクローズもできる(self):
        self.client.force_login(self.member)

        response = self.client.post(reverse("projects:task_create"), self._task_payload())

        self.assertEqual(response.status_code, 302)
        self.assertTrue(WbsTask.objects.filter(wbs_code="9.9").exists())

        response = self.client.post(reverse("projects:issue_close", args=[self.issue.pk]))

        self.assertEqual(response.status_code, 302)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.CLOSED)

    def test_案件メンバーは編集できても承認はできない(self):
        self.client.force_login(self.member)

        self.assertEqual(
            self.client.get(reverse("projects:change_edit", args=[self.change.pk])).status_code,
            200,
        )

        response = self.client.post(
            reverse("projects:change_decide", args=[self.change.pk]),
            {"decision": "approved", "reason": "メンバーは承認者ではない"},
        )

        self.assertEqual(response.status_code, 403)
        self.change.refresh_from_db()
        self.assertEqual(self.change.status, ChangeRequest.Status.PENDING_APPROVAL)

    def test_一覧の参照は参照専用ロールでも通る(self):
        """締めるのは書き込みだけ。見る導線を壊していないことを確かめる。"""

        self.client.force_login(self.viewer)

        for url_name in ("projects:list", "projects:issue_list", "projects:defect_list"):
            with self.subTest(url=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 200)
