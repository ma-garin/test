"""GE-04 / GE-05: 影響範囲ビューとグラフ品質ダッシュボード。

受入条件:
- GE-04「経路の各エッジの出所・確認状態を開け、候補を確定情報と誤認しない」
- GE-05「予測が弱い理由を、データ品質の作業として担当・期限つきで管理できる」
"""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.graph.models import Feature, MilestoneTaskLink, TaskDependency
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.graph.services.quality import build_quality_report, detect_cycles
from apps.projects.models import Defect, Milestone, Project, Severity, WbsTask


class ImpactViewTests(TestCase):
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
        self.feature = Feature.objects.create(project=self.project, name="受注登録")
        self.other_feature = Feature.objects.create(project=self.project, name="在庫引当")
        self.defect = Defect.objects.create(
            project=self.project, title="金額がずれる", severity=Severity.CRITICAL
        )
        WorkLink(
            relation_type=RelationType.IMPACTS,
            from_object=self.defect,
            to_object=self.feature,
            provenance=Provenance.MANUAL,
            state=LinkState.CONFIRMED,
            confirmed_by=self.user,
        ).save()
        WorkLink(
            relation_type=RelationType.IMPACTS,
            from_object=self.defect,
            to_object=self.other_feature,
            provenance=Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
        ).save()
        self.client.force_login(self.user)
        self.url = reverse("graph:impact", args=[self.defect.pk])

    def test_screen_opens(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/impact_view.html")

    def test_confirmed_and_candidate_are_separated(self):
        impact = self.client.get(self.url).context["impact"]
        self.assertEqual(len(impact.confirmed_edges), 1)
        self.assertEqual(len(impact.candidate_edges), 1)

    def test_candidate_is_labelled_as_unconfirmed(self):
        response = self.client.get(self.url)
        self.assertContains(response, "まだ確認されていない候補")
        self.assertContains(response, "確定情報ではありません")

    def test_each_edge_exposes_provenance_and_reviewer(self):
        response = self.client.get(self.url)
        self.assertContains(response, "手動登録")
        self.assertContains(response, str(self.user))

    def test_confirmed_only_count_is_shown_separately(self):
        response = self.client.get(self.url)
        self.assertLess(
            response.context["confirmed_node_count"], len(response.context["impact"].nodes)
        )

    def test_candidate_can_be_confirmed_from_the_screen(self):
        candidate = WorkLink.objects.get(state=LinkState.CANDIDATE)
        self.client.post(
            reverse("forecast:review_work_link", args=[candidate.pk]),
            {"action": "confirm", "next": self.url},
        )
        candidate.refresh_from_db()
        self.assertEqual(candidate.state, LinkState.CONFIRMED)
        self.assertEqual(candidate.confirmed_by, self.user)

    def test_other_tenant_defect_returns_404(self):
        other_tenant = Tenant.objects.create(code="beta", name="BETA")
        other_project = Project.objects.create(tenant=other_tenant, code="x1", name="別")
        foreign = Defect.objects.create(
            project=other_project, title="別テナント", severity=Severity.HIGH
        )
        response = self.client.get(reverse("graph:impact", args=[foreign.pk]))
        self.assertEqual(response.status_code, 404)


class GraphQualityTests(TestCase):
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
        self.client.force_login(self.user)

    def test_milestone_without_required_tasks_is_queued(self):
        Milestone.objects.create(
            project=self.project, name="結合完了", planned_date=date(2026, 9, 1)
        )
        report = build_quality_report(self.project)
        kinds = [item.kind for item in report.repairs]
        self.assertIn("milestone", kinds)

    def test_task_without_planned_end_is_queued(self):
        WbsTask.objects.create(project=self.project, wbs_code="A", name="期限なし")
        report = build_quality_report(self.project)
        targets = [item.target for item in report.repairs]
        self.assertTrue(any("期限なし" in target for target in targets))

    def test_feature_without_task_link_is_queued(self):
        Feature.objects.create(project=self.project, name="孤立した機能")
        report = build_quality_report(self.project)
        self.assertTrue(any(item.kind == "feature" for item in report.repairs))

    def test_dependency_coverage_counts_linked_tasks(self):
        milestone = Milestone.objects.create(
            project=self.project, name="結合完了", planned_date=date(2026, 9, 1)
        )
        linked = WbsTask.objects.create(
            project=self.project, wbs_code="A", name="紐付け済み", planned_end=date(2026, 9, 1)
        )
        WbsTask.objects.create(
            project=self.project, wbs_code="B", name="未紐付け", planned_end=date(2026, 9, 1)
        )
        MilestoneTaskLink.objects.create(milestone=milestone, task=linked).confirm(self.user)

        metric = build_quality_report(self.project).metric("dependency_coverage")
        self.assertEqual(metric.numerator, 1)
        self.assertEqual(metric.denominator, 2)
        self.assertEqual(metric.ratio, 0.5)

    def test_low_coverage_is_marked_as_degraded(self):
        for index in range(5):
            WbsTask.objects.create(
                project=self.project, wbs_code=f"W{index}", name=f"作業{index}"
            )
        metric = build_quality_report(self.project).metric("dependency_coverage")
        self.assertTrue(metric.is_degraded)

    def test_metric_without_target_is_not_degraded(self):
        metric = build_quality_report(self.project).metric("impact_coverage")
        self.assertFalse(metric.is_measurable)
        self.assertFalse(metric.is_degraded)
        self.assertEqual(metric.display, "対象なし")

    def test_edge_freshness_counts_recent_confirmations(self):
        feature = Feature.objects.create(project=self.project, name="受注登録")
        defect = Defect.objects.create(
            project=self.project, title="不具合", severity=Severity.CRITICAL
        )
        link = WorkLink(
            relation_type=RelationType.IMPACTS,
            from_object=defect,
            to_object=feature,
            provenance=Provenance.MANUAL,
            state=LinkState.CONFIRMED,
            confirmed_by=self.user,
            confirmed_at=timezone.now(),
        )
        link.save()
        metric = build_quality_report(self.project).metric("edge_freshness")
        self.assertEqual(metric.numerator, 1)

        WorkLink.objects.filter(pk=link.pk).update(
            confirmed_at=timezone.now() - timedelta(days=60)
        )
        metric = build_quality_report(self.project).metric("edge_freshness")
        self.assertEqual(metric.numerator, 0)

    def test_cycle_is_detected_with_its_path(self):
        first = WbsTask.objects.create(project=self.project, wbs_code="A", name="A")
        second = WbsTask.objects.create(project=self.project, wbs_code="B", name="B")
        TaskDependency.objects.create(predecessor=first, successor=second)
        TaskDependency.objects.bulk_create(
            [TaskDependency(project=self.project, predecessor=second, successor=first)]
        )
        cycles = detect_cycles(self.project)
        self.assertTrue(cycles)
        self.assertIn("A", cycles[0])

    def test_cycle_blocks_the_forecast(self):
        first = WbsTask.objects.create(project=self.project, wbs_code="A", name="A")
        second = WbsTask.objects.create(project=self.project, wbs_code="B", name="B")
        TaskDependency.objects.create(predecessor=first, successor=second)
        TaskDependency.objects.bulk_create(
            [TaskDependency(project=self.project, predecessor=second, successor=first)]
        )
        self.assertTrue(build_quality_report(self.project).blocks_forecast)

    def test_screen_lists_repairs(self):
        Milestone.objects.create(
            project=self.project, name="結合完了", planned_date=date(2026, 9, 1)
        )
        response = self.client.get(reverse("graph:quality"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "必須WBSの紐付け")
        self.assertGreater(response.context["repair_total"], 0)

    def test_screen_shows_the_consequence_not_only_the_number(self):
        response = self.client.get(reverse("graph:quality"))
        self.assertContains(response, "悪化したときに起きること")

    def test_other_tenant_project_is_not_listed(self):
        other_tenant = Tenant.objects.create(code="beta", name="BETA")
        Project.objects.create(tenant=other_tenant, code="x1", name="他テナント案件")
        response = self.client.get(reverse("graph:quality"))
        self.assertNotContains(response, "他テナント案件")

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("graph:quality")).status_code, 302)

    def test_metric_without_threshold_is_shown_as_reference(self):
        """基準を持たない指標を「基準内」と表示しない。"""
        metric = build_quality_report(self.project).metric("edge_freshness")
        self.assertFalse(metric.has_threshold)
        self.assertIn(metric.verdict, ("参考値", "対象なし"))

    def test_project_without_milestones_is_queued(self):
        """指標が 0% なのに整備キューが空、という食い違いを作らない。"""
        WbsTask.objects.create(
            project=self.project, wbs_code="A", name="作業A", planned_end=date(2026, 9, 1)
        )
        report = build_quality_report(self.project)
        self.assertTrue(report.metric("dependency_coverage").is_degraded)
        self.assertTrue(any(item.label == "マイルストーンの登録" for item in report.repairs))


class EdgeLabelTests(TestCase):
    """画面に内部キーを出さない（GE-04）。"""

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
        feature = Feature.objects.create(project=self.project, name="受注登録")
        self.defect = Defect.objects.create(
            project=self.project, title="不具合", severity=Severity.CRITICAL
        )
        WorkLink(
            relation_type=RelationType.IMPACTS,
            from_object=self.defect,
            to_object=feature,
            provenance=Provenance.MANUAL,
            state=LinkState.CONFIRMED,
            confirmed_by=self.user,
        ).save()
        self.client.force_login(self.user)

    def test_relation_and_provenance_are_shown_in_japanese(self):
        response = self.client.get(reverse("graph:impact", args=[self.defect.pk]))
        self.assertContains(response, "影響する")
        self.assertContains(response, "手動登録")

    def test_raw_keys_are_not_rendered_as_labels(self):
        response = self.client.get(reverse("graph:impact", args=[self.defect.pk]))
        content = response.content.decode()
        self.assertNotIn(">impacts<", content)
        self.assertNotIn(">manual<", content)
