"""GE-03: 影響・依存の経路クエリ。

受入条件「画面・予測・報告が同じ経路と根拠を再利用し、N+1クエリや全件走査に
依存しない」を、経路の中身と発行クエリ数の両方で確認する。
"""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.graph.models import Component, Feature, MilestoneTaskLink, TaskDependency, WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.graph.services.queries import build_impact, downstream_tasks, milestones_for_tasks
from apps.projects.models import Defect, Milestone, Project, Severity, WbsTask


class ImpactQueryTests(TestCase):
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
        self.api = Component.objects.create(
            project=self.project, name="注文API", kind=Component.Kind.API
        )
        self.defect = Defect.objects.create(
            project=self.project, title="金額がずれる", severity=Severity.CRITICAL
        )
        self.task = WbsTask.objects.create(
            project=self.project, wbs_code="3.2", name="結合試験"
        )

        self._link(self.defect, self.feature, RelationType.IMPACTS, Provenance.EXTERNAL_ID)
        self._link(self.api, self.feature, RelationType.IMPLEMENTS, Provenance.MANUAL)
        self._link(self.defect, self.task, RelationType.BLOCKS, Provenance.MANUAL)

    def _link(self, source, target, relation, provenance, state=LinkState.CONFIRMED) -> WorkLink:
        link = WorkLink(
            relation_type=relation,
            from_object=source,
            to_object=target,
            provenance=provenance,
            state=state,
            confirmed_by=self.user if state == LinkState.CONFIRMED else None,
        )
        link.save()
        return link

    def test_impact_reaches_feature_and_task(self):
        result = build_impact(self.defect)
        labels = {node.label for node in result.nodes}
        self.assertIn("graph.feature", labels)
        self.assertIn("projects.wbstask", labels)

    def test_nodes_carry_display_titles(self):
        result = build_impact(self.defect)
        features = result.by_label("graph.feature")
        self.assertEqual(features[0].title, "受注登録")

    def test_edges_expose_provenance_and_state(self):
        result = build_impact(self.defect)
        edge = next(e for e in result.edges if e.relation_type == RelationType.IMPACTS)
        self.assertEqual(edge.provenance, Provenance.EXTERNAL_ID)
        self.assertTrue(edge.is_confirmed)
        self.assertEqual(edge.confirmed_by, str(self.user))

    def test_candidates_are_separated_from_confirmed(self):
        other = Feature.objects.create(project=self.project, name="在庫引当")
        self._link(
            self.defect, other, RelationType.IMPACTS, Provenance.AI_CANDIDATE, LinkState.CANDIDATE
        )
        result = build_impact(self.defect)
        self.assertEqual(len(result.candidate_edges), 1)
        self.assertEqual(len(result.confirmed_edges), 3)

    def test_forecast_path_excludes_candidates(self):
        """予測は確定リンクだけをたどる。候補で納期を動かさない。"""
        other = Feature.objects.create(project=self.project, name="在庫引当")
        self._link(
            self.defect, other, RelationType.IMPACTS, Provenance.AI_CANDIDATE, LinkState.CANDIDATE
        )
        result = build_impact(self.defect, include_candidates=False)
        self.assertNotIn("在庫引当", [node.title for node in result.nodes])

    def test_rejected_links_are_not_traversed(self):
        other = Feature.objects.create(project=self.project, name="否定された機能")
        link = self._link(
            self.defect, other, RelationType.IMPACTS, Provenance.AI_CANDIDATE, LinkState.CANDIDATE
        )
        link.reject(self.user, reason="別機能")
        result = build_impact(self.defect)
        self.assertNotIn("否定された機能", [node.title for node in result.nodes])

    def test_other_project_links_are_not_visible(self):
        other_project = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        other_defect = Defect.objects.create(
            project=other_project, title="別案件の不具合", severity=Severity.HIGH
        )
        other_feature = Feature.objects.create(project=other_project, name="別案件機能")
        self._link(other_defect, other_feature, RelationType.IMPACTS, Provenance.MANUAL)

        result = build_impact(self.defect)
        self.assertNotIn("別案件機能", [node.title for node in result.nodes])

    def test_impact_reaches_implementing_component(self):
        """機能が壊れれば、それを実装している技術要素も確認対象になる。"""
        result = build_impact(self.defect)
        components = result.by_label("graph.component")
        self.assertEqual([node.id for node in components], [self.api.pk])

    def test_depth_limit_is_reported(self):
        result = build_impact(self.defect, max_depth=1)
        self.assertNotIn("graph.component", {node.label for node in result.nodes})

    def test_query_count_does_not_grow_with_graph_size(self):
        """N+1 の回帰検出。ノード数を増やしてもクエリ数が変わらないこと。

        絶対値を固定すると、無関係な変更で落ちる代わりに N+1 は見逃す。
        「増えないこと」を直接確かめる。
        """
        with CaptureQueriesContext(connection) as small:
            build_impact(self.defect)

        for index in range(10):
            extra = Feature.objects.create(project=self.project, name=f"追加機能{index}")
            self._link(self.defect, extra, RelationType.IMPACTS, Provenance.MANUAL)

        with CaptureQueriesContext(connection) as large:
            build_impact(self.defect)

        self.assertEqual(len(large.captured_queries), len(small.captured_queries))
        self.assertLessEqual(len(large.captured_queries), 8)


class DependencyQueryTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.tasks = {
            code: WbsTask.objects.create(project=self.project, wbs_code=code, name=f"作業{code}")
            for code in ("A", "B", "C", "D")
        }
        TaskDependency.objects.create(
            predecessor=self.tasks["A"], successor=self.tasks["B"], lag_business_days=1
        )
        TaskDependency.objects.create(predecessor=self.tasks["B"], successor=self.tasks["C"])
        TaskDependency.objects.create(predecessor=self.tasks["A"], successor=self.tasks["D"])

    def test_downstream_includes_transitive_successors(self):
        ids = [item[0] for item in downstream_tasks(self.tasks["A"])]
        self.assertCountEqual(
            ids, [self.tasks["B"].pk, self.tasks["D"].pk, self.tasks["C"].pk]
        )

    def test_downstream_keeps_lag(self):
        lags = {item[0]: item[1] for item in downstream_tasks(self.tasks["A"])}
        self.assertEqual(lags[self.tasks["B"].pk], 1)
        self.assertEqual(lags[self.tasks["D"].pk], 0)

    def test_downstream_of_leaf_is_empty(self):
        self.assertEqual(downstream_tasks(self.tasks["C"]), ())

    def test_downstream_uses_one_query(self):
        with self.assertNumQueries(1):
            downstream_tasks(self.tasks["A"])

    def test_milestones_for_tasks_is_batched(self):
        milestone = Milestone.objects.create(
            project=self.project, name="結合完了", planned_date=date(2026, 9, 1)
        )
        MilestoneTaskLink.objects.create(milestone=milestone, task=self.tasks["C"])
        MilestoneTaskLink.objects.create(
            milestone=milestone, task=self.tasks["D"], is_required=False
        )

        with self.assertNumQueries(1):
            mapping = milestones_for_tasks([t.pk for t in self.tasks.values()])

        self.assertEqual(mapping, {self.tasks["C"].pk: [milestone.pk]})
