"""マイルストーンの予実差分析（要件 #4）。

「実績も見込も入っていない期日切れ」を遅れ 0 と数えないことが肝心。
そこを見逃すと、入力されていないだけの節目が「計画どおり」に見える。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.services.milestones import build_milestone_report
from apps.projects.models import Milestone, Project, ProjectMember

TODAY = timezone.localdate()


class MilestoneReportTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.projects = Project.objects.filter(pk=self.project.pk)

    def _milestone(self, **kwargs) -> Milestone:
        defaults = {"project": self.project, "name": "設計完了", "planned_date": TODAY}
        defaults.update(kwargs)

        return Milestone.objects.create(**defaults)

    def test_実績日が計画日より後ろなら遅延として数える(self) -> None:
        self._milestone(planned_date=TODAY - timedelta(days=10), actual_date=TODAY - timedelta(days=7))

        report = build_milestone_report(self.projects, today=TODAY)

        row = report.rows[0]
        self.assertEqual(row.slip_days, 3)
        self.assertTrue(row.is_done)
        self.assertEqual(row.state_label, "達成（遅延）")
        self.assertEqual(report.late_count, 1)

    def test_実績日が計画日より前なら前倒しとして出す(self) -> None:
        self._milestone(planned_date=TODAY, actual_date=TODAY - timedelta(days=2))

        report = build_milestone_report(self.projects, today=TODAY)

        row = report.rows[0]
        self.assertEqual(row.slip_days, -2)
        self.assertEqual(row.slip_label, "2日 前倒し")
        self.assertEqual(row.state_label, "達成")
        self.assertEqual(report.late_count, 0)

    def test_見込日があれば見込日と比較する(self) -> None:
        self._milestone(planned_date=TODAY, forecast_date=TODAY + timedelta(days=6))

        row = build_milestone_report(self.projects, today=TODAY).rows[0]

        self.assertEqual(row.slip_days, 6)
        self.assertEqual(row.basis, "見込日と比較")
        self.assertEqual(row.state_label, "遅延")

    def test_実績も見込も無い期日切れを遅れ0にしない(self) -> None:
        """入力されていないだけの節目を「計画どおり」と表示しない。"""

        self._milestone(planned_date=TODAY - timedelta(days=4))

        row = build_milestone_report(self.projects, today=TODAY).rows[0]

        self.assertEqual(row.slip_days, 4)
        self.assertTrue(row.is_late)
        self.assertEqual(row.basis, "実績・見込とも未入力のため本日と比較")

    def test_期日が未到来ならずれなしとして扱う(self) -> None:
        self._milestone(planned_date=TODAY + timedelta(days=5))

        row = build_milestone_report(self.projects, today=TODAY).rows[0]

        self.assertEqual(row.slip_days, 0)
        self.assertFalse(row.is_late)
        self.assertEqual(row.basis, "計画日は未到来")

    def test_品質ゲートの遅れを別に数える(self) -> None:
        self._milestone(name="結合試験完了", planned_date=TODAY - timedelta(days=9), is_gate=True)
        self._milestone(name="設計完了", planned_date=TODAY - timedelta(days=2))

        report = build_milestone_report(self.projects, today=TODAY)

        self.assertEqual(report.late_count, 2)
        self.assertEqual(report.gate_late_count, 1)
        self.assertEqual(report.max_slip_days, 9)

    def test_他案件のマイルストーンは含めない(self) -> None:
        other = Project.objects.create(tenant=self.tenant, code="p2", name="別案件")
        Milestone.objects.create(project=other, name="他案件の節目", planned_date=TODAY)
        self._milestone()

        report = build_milestone_report(self.projects, today=TODAY)

        self.assertEqual(report.total, 1)
        self.assertEqual(report.rows[0].project, self.project)

    def test_達成済みは予定一覧に出さない(self) -> None:
        self._milestone(name="完了済み", actual_date=TODAY)
        self._milestone(name="これから", planned_date=TODAY + timedelta(days=3))

        report = build_milestone_report(self.projects, today=TODAY)

        self.assertEqual([row.milestone.name for row in report.upcoming], ["これから"])


class MilestoneViewTests(TestCase):
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

    def test_進捗画面にマイルストーンの予実が出る(self) -> None:
        Milestone.objects.create(
            project=self.project,
            name="結合試験完了",
            planned_date=TODAY - timedelta(days=8),
            is_gate=True,
        )

        response = self.client.get(reverse("dashboard:progress"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "結合試験完了")
        self.assertContains(response, "8日 後ろ倒し")

    def test_未登録でも画面が壊れない(self) -> None:
        response = self.client.get(reverse("dashboard:progress"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "マイルストーンが登録されていません")
