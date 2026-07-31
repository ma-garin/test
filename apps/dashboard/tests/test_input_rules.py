"""入力標準ルールの運用支援（要件 #47）。

「対象 0 件」を遵守率 100% と言わないこと、Blocked 運用の 2 ルールが
別々に効くことを固定する。ここが甘いと、入力が空でも健全に見える。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.services.input_rules import STALE_AFTER_DAYS, build_input_rule_report
from apps.projects.models import Priority, Project, ProjectMember, WbsTask

TODAY = timezone.localdate()


class InputRuleTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.projects = Project.objects.filter(pk=self.project.pk)

    def _task(self, **kwargs) -> WbsTask:
        defaults = {
            "project": self.project,
            "wbs_code": kwargs.pop("code", "1.1"),
            "name": "タスク",
            "owner": "担当者",
            "status": WbsTask.Status.IN_PROGRESS,
            "priority": Priority.MEDIUM,
            "planned_end": TODAY + timedelta(days=5),
        }
        defaults.update(kwargs)

        return WbsTask.objects.create(**defaults)

    def _result(self, key: str):
        report = build_input_rule_report(self.projects, today=TODAY)

        return next(result for result in report.results if result.key == key)

    def test_担当が空のタスクを違反として挙げる(self) -> None:
        self._task(owner="")

        result = self._result("owner_required")

        self.assertEqual(result.violation_count, 1)
        self.assertEqual(result.compliance_percent, 0)

    def test_完了済みタスクは担当ルールの対象外(self) -> None:
        self._task(owner="", status=WbsTask.Status.DONE, progress_percent=100)

        result = self._result("owner_required")

        self.assertEqual(result.target_count, 0)
        self.assertFalse(result.has_target)
        self.assertEqual(result.state_label, "対象なし")

    def test_期限未設定を違反として挙げる(self) -> None:
        self._task(planned_end=None)

        self.assertEqual(self._result("due_date_required").violation_count, 1)

    def test_Blockedのボール保持者と次アクションを別々に判定する(self) -> None:
        self._task(
            status=WbsTask.Status.BLOCKED, ball_holder="顧客", next_action="", code="1.1"
        )
        self._task(
            status=WbsTask.Status.BLOCKED, ball_holder="", next_action="回答を待つ", code="1.2"
        )

        self.assertEqual(self._result("blocked_needs_ball_holder").violation_count, 1)
        self.assertEqual(self._result("blocked_needs_next_action").violation_count, 1)

    def test_更新が止まった進行中タスクを検出する(self) -> None:
        task = self._task()
        stale = timezone.now() - timedelta(days=STALE_AFTER_DAYS + 1)
        WbsTask.objects.filter(pk=task.pk).update(updated_at=stale)

        self.assertEqual(self._result("weekly_update").violation_count, 1)

    def test_直近に更新された進行中タスクは違反にしない(self) -> None:
        self._task()

        result = self._result("weekly_update")

        self.assertEqual(result.violation_count, 0)
        self.assertEqual(result.compliance_percent, 100)

    def test_完了なのに進捗が100未満なら食い違いとして挙げる(self) -> None:
        self._task(status=WbsTask.Status.DONE, progress_percent=80)

        self.assertEqual(self._result("progress_consistency").violation_count, 1)

    def test_進捗100なのに未完了なら食い違いとして挙げる(self) -> None:
        self._task(status=WbsTask.Status.IN_PROGRESS, progress_percent=100)

        self.assertEqual(self._result("progress_consistency").violation_count, 1)

    def test_全て満たしていれば違反ゼロ(self) -> None:
        self._task()

        report = build_input_rule_report(self.projects, today=TODAY)

        self.assertEqual(report.violation_total, 0)
        self.assertEqual(report.tone, "g")
        self.assertIn("違反はありません", report.summary)

    def test_アーカイブ済みタスクは数えない(self) -> None:
        self._task(status=WbsTask.Status.ARCHIVED, owner="", planned_end=None)

        report = build_input_rule_report(self.projects, today=TODAY)

        self.assertEqual(report.task_total, 0)
        self.assertEqual(report.violation_total, 0)

    def test_他案件のタスクは含めない(self) -> None:
        other = Project.objects.create(tenant=self.tenant, code="p2", name="別案件")
        WbsTask.objects.create(
            project=other, wbs_code="9.9", name="他案件", owner="", priority=Priority.MEDIUM
        )
        self._task()

        report = build_input_rule_report(self.projects, today=TODAY)

        self.assertEqual(report.task_total, 1)


class InputRuleViewTests(TestCase):
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
        session["tenant_id"] = str(self.tenant.pk)
        session.save()

        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        ProjectMember.objects.create(project=self.project, user=self.user, role_label="PMO")

    def test_タスク一覧に遵守状況が出る(self) -> None:
        WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="設計",
            owner="",
            status=WbsTask.Status.IN_PROGRESS,
            priority=Priority.MEDIUM,
        )

        response = self.client.get(reverse("dashboard:tasks"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "入力ルールの遵守状況")
        self.assertContains(response, "担当を空にしない")

    def test_絞り込んでも案件全体で数える(self) -> None:
        """絞り込みの中だけで数えると、違反が隠れて見える。"""

        WbsTask.objects.create(
            project=self.project,
            wbs_code="1.1",
            name="設計",
            owner="",
            status=WbsTask.Status.IN_PROGRESS,
            priority=Priority.MEDIUM,
        )

        response = self.client.get(reverse("dashboard:tasks"), {"status": WbsTask.Status.DONE})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["input_rules"].violation_total, 2)
