"""LDF-08: 日次・週次報告と通知候補。

受入条件「前回との差分と根拠を含む報告を、確認後に共有できる」。
AI の推定を確定事項として載せないこと、通知を乱発しないことを固定する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.forecast.models import Confidence, ForecastSnapshot, Horizon, MissingInput
from apps.forecast.services.recompute import recompute_project
from apps.forecast.services.report import DAILY_WINDOW, WEEKLY_WINDOW, build_report
from apps.graph.models import Feature, MilestoneTaskLink, WorkingCalendar
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import Provenance, RelationType
from apps.projects.models import Milestone, Project, WbsTask


class ReportTests(TestCase):
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
            project=self.project, name="結合試験完了", planned_date=self.today + timedelta(days=5)
        )
        self.task = WbsTask.objects.create(
            project=self.project,
            wbs_code="3.2",
            name="結合試験",
            planned_end=self.today + timedelta(days=5),
            status=WbsTask.Status.IN_PROGRESS,
        )
        MilestoneTaskLink.objects.create(milestone=self.milestone, task=self.task).confirm(
            self.user
        )
        self.client.force_login(self.user)

    def test_quiet_report_is_distinguished_from_empty(self):
        draft = build_report(self.project)
        self.assertTrue(draft.is_quiet)
        self.assertIn("変化はありません", draft.as_text())

    def test_first_forecast_appears_as_a_new_change(self):
        recompute_project(self.project, self.today)
        draft = build_report(self.project)
        self.assertTrue(draft.notable_changes)
        self.assertEqual(draft.notable_changes[0].direction, "新規")

    def test_worsening_is_reported_with_the_delta(self):
        recompute_project(self.project, self.today)
        self.task.planned_end = self.today + timedelta(days=12)
        self.task.save()
        recompute_project(self.project, self.today)

        draft = build_report(self.project)
        worsened = [c for c in draft.changes if c.direction == "悪化"]
        self.assertTrue(worsened)
        self.assertIn("営業日 悪化", worsened[0].describe())

    def test_improvement_is_reported_but_not_notified(self):
        self.task.planned_end = self.today + timedelta(days=12)
        self.task.save()
        recompute_project(self.project, self.today)

        self.task.planned_end = self.today + timedelta(days=5)
        self.task.save()
        recompute_project(self.project, self.today)

        draft = build_report(self.project)
        improved = [c for c in draft.changes if c.direction == "改善"]
        self.assertTrue(improved)
        self.assertNotIn(improved[0], draft.notifications)

    def test_becoming_undeterminable_is_notified(self):
        recompute_project(self.project, self.today)
        self.task.planned_end = None
        self.task.save()
        recompute_project(self.project, self.today)

        draft = build_report(self.project)
        self.assertTrue(draft.notifications)
        self.assertEqual(draft.notifications[0].direction, "算定不能化")

    def test_undeterminable_items_list_the_missing_inputs(self):
        self.task.planned_end = None
        self.task.save()
        recompute_project(self.project, self.today)

        draft = build_report(self.project)
        self.assertTrue(draft.undeterminable)
        self.assertIn("計画終了日", draft.as_text())

    def test_unconfirmed_links_are_listed_separately(self):
        feature = Feature.objects.create(project=self.project, name="受注登録")
        WorkLink(
            relation_type=RelationType.IMPLEMENTS,
            from_object=self.task,
            to_object=feature,
            provenance=Provenance.AI_CANDIDATE,
        ).save()

        draft = build_report(self.project)
        self.assertEqual(len(draft.unconfirmed_links), 1)
        self.assertIn("未確認事項", draft.as_text())

    def test_draft_states_that_it_is_not_confirmed(self):
        draft = build_report(self.project)
        self.assertIn("下書き", draft.as_text())
        self.assertIn("未確認事項を確認してから共有", draft.as_text())

    def test_pending_reviews_are_listed(self):
        recompute_project(self.project, self.today)
        draft = build_report(self.project)
        self.assertTrue(draft.pending_reviews)

    def test_weekly_window_covers_more_than_daily(self):
        old = timezone.now() - timedelta(days=3)
        ForecastSnapshot.objects.create(
            project=self.project,
            target=self.milestone,
            as_of=old,
            horizon=Horizon.MILESTONE,
            confidence=Confidence.UNKNOWN,
            missing_inputs=[MissingInput.NO_DEPENDENCY],
        )
        self.assertEqual(len(build_report(self.project, window=DAILY_WINDOW).changes), 0)
        self.assertEqual(len(build_report(self.project, window=WEEKLY_WINDOW).changes), 1)

    def test_freshness_note_is_included(self):
        draft = build_report(self.project)
        self.assertIn("情報", draft.freshness_note)

    # ── 画面 ─────────────────────────────────────────────────
    def test_report_screen_opens(self):
        response = self.client.get(reverse("forecast:report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "これは下書きです")

    def test_report_screen_does_not_send_externally(self):
        response = self.client.get(reverse("forecast:report"))
        self.assertContains(response, "外部システムへは送信しません")

    def test_weekly_range_is_selectable(self):
        response = self.client.get(reverse("forecast:report"), {"range": "weekly"})
        self.assertTrue(response.context["is_weekly"])

    def test_other_tenant_is_not_included(self):
        other_tenant = Tenant.objects.create(code="beta", name="BETA")
        Project.objects.create(tenant=other_tenant, code="x1", name="他テナント案件")
        response = self.client.get(reverse("forecast:report"))
        self.assertNotContains(response, "他テナント案件")

    def test_snapshot_can_be_adopted_from_the_report(self):
        recompute_project(self.project, self.today)
        snapshot = ForecastSnapshot.objects.filter(horizon=Horizon.MILESTONE).first()
        self.client.post(
            reverse("forecast:review_snapshot", args=[snapshot.pk]),
            {"decision": "adopt", "next": reverse("forecast:report")},
        )
        self.assertEqual(snapshot.reviews.count(), 1)

    def test_sections_do_not_say_empty_when_they_have_rows(self):
        """行があるのに「ありません」を併記しない（`extend or append` の罠）。"""
        recompute_project(self.project, self.today)
        text = build_report(self.project).as_text()
        head = text.split("## 算定不能の項目")[0]
        self.assertIn("結合試験完了", head)
        self.assertNotIn("変化はありません", head)

    def test_change_names_include_the_horizon(self):
        """3時点の予測が同じ行に見えないこと。"""
        recompute_project(self.project, self.today)
        names = [change.target_name for change in build_report(self.project).notable_changes]
        self.assertEqual(len(set(names)), len(names))

    def test_hidden_changes_are_reported_not_silently_dropped(self):
        for index in range(15):
            milestone = Milestone.objects.create(
                project=self.project,
                name=f"ゲート{index}",
                planned_date=self.today + timedelta(days=5),
            )
            MilestoneTaskLink.objects.create(milestone=milestone, task=self.task).confirm(
                self.user
            )
        recompute_project(self.project, self.today)

        draft = build_report(self.project)
        self.assertGreater(draft.hidden_change_count, 0)
        self.assertIn("ほか", draft.as_text())
