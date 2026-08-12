"""LDF-04: ライブ着地予測の画面。

受入条件「PMOが30秒以内に危険な着地・根拠・次アクションを確認できる」を、
「最初の表で危険な順に出ているか」「算定不能を 0 日と見せていないか」
「権限外の案件が混ざらないか」で確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.graph.models import Feature, MilestoneTaskLink, WorkingCalendar
from apps.projects.models import Milestone, Project, ProjectMember, WbsTask


class LiveForecastViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        WorkingCalendar.objects.create(project=self.project)
        self.today = timezone.localdate()
        self.milestone = Milestone.objects.create(
            project=self.project, name="結合試験完了", planned_date=self.today + timedelta(days=3)
        )
        self.task = WbsTask.objects.create(
            project=self.project,
            wbs_code="3.2",
            name="結合試験",
            planned_end=self.today + timedelta(days=10),
            status=WbsTask.Status.IN_PROGRESS,
        )
        MilestoneTaskLink.objects.create(milestone=self.milestone, task=self.task).confirm(
            self.user
        )
        self.client.force_login(self.user)

    def test_screen_opens(self):
        response = self.client.get(reverse("forecast:live"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/live_forecast.html")

    def test_delayed_milestone_is_listed_first(self):
        Milestone.objects.create(
            project=self.project, name="安全なゲート", planned_date=self.today + timedelta(days=90)
        )
        board = self.client.get(reverse("forecast:live")).context["board"]
        self.assertEqual(board.top_risks[0].target_name, "結合試験完了")

    def test_delay_is_shown_in_business_days(self):
        board = self.client.get(reverse("forecast:live")).context["board"]
        self.assertIn("営業日 遅延", board.top_risks[0].variance_label)

    def test_undeterminable_is_not_shown_as_zero_days(self):
        self.task.planned_end = None
        self.task.save()
        response = self.client.get(reverse("forecast:live"))
        board = response.context["board"]
        row = board.top_risks[0]
        self.assertEqual(row.variance_label, "算定不能")
        self.assertContains(response, "算定不能")

    def test_missing_calendar_is_reported_not_guessed(self):
        WorkingCalendar.objects.filter(project=self.project).delete()
        response = self.client.get(reverse("forecast:live"))
        self.assertContains(response, "勤務カレンダー未設定の案件があります")
        self.assertIn("p1", response.context["board"].projects_without_calendar)

    def test_empty_state_explains_the_missing_input(self):
        MilestoneTaskLink.objects.all().delete()
        Milestone.objects.all().delete()
        response = self.client.get(reverse("forecast:live"))
        self.assertContains(response, "着地を計算できるマイルストーンがありません")

    def test_other_tenant_projects_are_not_visible(self):
        other_tenant = Tenant.objects.create(code="beta", name="BETA")
        other_project = Project.objects.create(
            tenant=other_tenant, code="x1", name="他テナント案件"
        )
        WorkingCalendar.objects.create(project=other_project)
        Milestone.objects.create(
            project=other_project, name="他テナントのゲート", planned_date=self.today
        )

        response = self.client.get(reverse("forecast:live"))
        self.assertNotContains(response, "他テナントのゲート")

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(reverse("forecast:live"))
        self.assertEqual(response.status_code, 302)


class FeatureDetailViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        WorkingCalendar.objects.create(project=self.project)
        self.feature = Feature.objects.create(
            project=self.project, name="受注登録", owner="山田"
        )
        self.client.force_login(self.user)

    def test_detail_opens(self):
        response = self.client.get(
            reverse("forecast:feature_detail", args=[self.feature.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "受注登録")

    def test_detail_explains_why_the_forecast_is_missing(self):
        response = self.client.get(
            reverse("forecast:feature_detail", args=[self.feature.pk])
        )
        self.assertContains(response, "着地を算定できません")

    def test_detail_shows_next_action(self):
        response = self.client.get(
            reverse("forecast:feature_detail", args=[self.feature.pk])
        )
        self.assertContains(response, "次アクション")
        self.assertContains(response, response.context["detail"].next_action)

    def test_feature_from_another_tenant_returns_404(self):
        other_tenant = Tenant.objects.create(code="beta", name="BETA")
        other_project = Project.objects.create(tenant=other_tenant, code="x1", name="別")
        foreign = Feature.objects.create(project=other_project, name="別テナント機能")

        response = self.client.get(reverse("forecast:feature_detail", args=[foreign.pk]))
        self.assertEqual(response.status_code, 404)

    def test_member_without_project_access_cannot_open_it(self):
        member = User.objects.create_user(
            username="member",
            email="member@example.com",
            password="pw",
            tenant=self.tenant,
            role=Role.PMO,
        )
        other_project = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        ProjectMember.objects.create(project=other_project, user=member)
        self.client.force_login(member)

        response = self.client.get(
            reverse("forecast:feature_detail", args=[self.feature.pk])
        )
        self.assertIn(response.status_code, (403, 404))
