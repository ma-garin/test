"""課題・不具合一覧の GET 絞り込み（UXP-07 / UXP-08）。

見るのは外部から観測できる挙動だけ。
1. 条件を付けると行が減ること
2. 条件を外すと必ず全件へ戻れること（袋小路を作らない）
3. 0 件の理由が「未登録」なのか「絞り込みの結果」なのか区別できること
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.projects.models import Defect, Issue, Project, ProjectMember, Severity


class ListFilterTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="a", name="テナントA")
        self.project = Project.objects.create(tenant=self.tenant, code="a1", name="A案件1")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.PMO
        )
        self.client.force_login(self.user)
        self.today = timezone.localdate()


class IssueListFilterTests(ListFilterTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("projects:issue_list")
        self.overdue = Issue.objects.create(
            project=self.project,
            title="期限超過の課題",
            status=Issue.Status.OPEN,
            severity=Severity.HIGH,
            due_date=self.today - timedelta(days=3),
        )
        self.blocked = Issue.objects.create(
            project=self.project,
            title="ブロック中の課題",
            status=Issue.Status.BLOCKED,
            severity=Severity.LOW,
            due_date=self.today + timedelta(days=30),
        )
        self.no_due = Issue.objects.create(
            project=self.project,
            title="期限なしの課題",
            status=Issue.Status.OPEN,
            severity=Severity.MEDIUM,
        )

    def _titles(self, response) -> set[str]:
        return {issue.title for issue in response.context["issues"]}

    def test_status_filter_narrows_rows(self) -> None:
        response = self.client.get(self.url, {"status": Issue.Status.BLOCKED})

        self.assertEqual(self._titles(response), {"ブロック中の課題"})
        self.assertIn("状態: ブロック中", response.context["filters"]["applied"])

    def test_severity_and_due_filters_narrow_rows(self) -> None:
        overdue_response = self.client.get(self.url, {"due": "overdue"})
        none_response = self.client.get(self.url, {"due": "none"})
        severity_response = self.client.get(self.url, {"severity": Severity.LOW})

        self.assertEqual(self._titles(overdue_response), {"期限超過の課題"})
        self.assertEqual(self._titles(none_response), {"期限なしの課題"})
        self.assertEqual(self._titles(severity_response), {"ブロック中の課題"})

    def test_clearing_conditions_returns_all_rows(self) -> None:
        filtered = self.client.get(self.url, {"status": Issue.Status.BLOCKED})
        cleared = self.client.get(self.url)

        self.assertEqual(len(filtered.context["issues"]), 1)
        self.assertEqual(len(cleared.context["issues"]), 3)
        self.assertFalse(cleared.context["is_filtered"])
        self.assertContains(cleared, "条件をクリア")
        self.assertContains(cleared, "並び順")

    def test_zero_result_message_differs_from_unregistered(self) -> None:
        filtered = self.client.get(self.url, {"severity": Severity.CRITICAL})
        Issue.objects.all().delete()
        empty = self.client.get(self.url)

        self.assertEqual(len(filtered.context["issues"]), 0)
        self.assertContains(filtered, "絞り込み条件に一致する課題がありません")
        self.assertContains(empty, "課題はまだ未登録です")

    def test_overdue_marker_is_shown_in_row(self) -> None:
        response = self.client.get(self.url)
        flags = {issue.title: issue.is_overdue for issue in response.context["issues"]}

        self.assertTrue(flags["期限超過の課題"])
        self.assertFalse(bool(flags["ブロック中の課題"]))
        self.assertContains(response, "期限超過")
        self.assertContains(response, "担当未設定")
        self.assertContains(response, "外部キー未連携")
        self.assertContains(response, "内容を確認・編集")


class DefectListFilterTests(ListFilterTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("projects:defect_list")
        self.critical = Defect.objects.create(
            project=self.project,
            title="未解決の重大不具合",
            status=Defect.Status.FIXING,
            severity=Severity.CRITICAL,
            phase="結合テスト",
        )
        self.closed_critical = Defect.objects.create(
            project=self.project,
            title="完了済みの重大不具合",
            status=Defect.Status.CLOSED,
            severity=Severity.CRITICAL,
            phase="結合テスト",
        )
        self.minor = Defect.objects.create(
            project=self.project,
            title="未解決の軽微な不具合",
            status=Defect.Status.NEW,
            severity=Severity.LOW,
            phase="単体テスト",
        )

    def _titles(self, response) -> set[str]:
        return {defect.title for defect in response.context["defects"]}

    def test_quick_view_shows_unresolved_and_critical_only(self) -> None:
        response = self.client.get(self.url, {"quick": "unresolved_critical"})

        self.assertEqual(self._titles(response), {"未解決の重大不具合"})
        self.assertIn("未解決かつ重大", response.context["filters"]["applied"])

    def test_status_severity_and_phase_filters_narrow_rows(self) -> None:
        status_response = self.client.get(self.url, {"status": Defect.Status.CLOSED})
        severity_response = self.client.get(self.url, {"severity": Severity.LOW})
        phase_response = self.client.get(self.url, {"phase": "単体テスト"})

        self.assertEqual(self._titles(status_response), {"完了済みの重大不具合"})
        self.assertEqual(self._titles(severity_response), {"未解決の軽微な不具合"})
        self.assertEqual(self._titles(phase_response), {"未解決の軽微な不具合"})

    def test_clearing_conditions_returns_all_rows(self) -> None:
        filtered = self.client.get(self.url, {"quick": "unresolved_critical"})
        cleared = self.client.get(self.url)

        self.assertEqual(len(filtered.context["defects"]), 1)
        self.assertEqual(len(cleared.context["defects"]), 3)
        self.assertFalse(cleared.context["is_filtered"])
        self.assertContains(cleared, "条件をクリア")
        self.assertContains(cleared, "取り消せません")

    def test_zero_result_message_differs_from_unregistered(self) -> None:
        filtered = self.client.get(
            self.url, {"phase": "単体テスト", "severity": Severity.CRITICAL}
        )
        Defect.objects.all().delete()
        empty = self.client.get(self.url)

        self.assertEqual(len(filtered.context["defects"]), 0)
        self.assertContains(filtered, "絞り込み条件に一致する不具合がありません")
        self.assertContains(empty, "不具合はまだ未登録です")

    def test_unknown_filter_value_is_ignored(self) -> None:
        response = self.client.get(self.url, {"status": "not-a-status", "quick": "x"})

        self.assertEqual(len(response.context["defects"]), 3)
        self.assertFalse(response.context["is_filtered"])
