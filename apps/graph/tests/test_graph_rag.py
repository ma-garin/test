"""GE-06: 確定リンクだけを使う GraphRAG の受入確認。

受入条件「ベクトル検索だけより根拠経路を説明でき、権限・鮮度・出所の条件を維持する」を、
経路の中身（出所・確認者）・候補の除外・案件境界・鮮度・ベクトルとの差分で確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, FileType
from apps.forecast.models.signals import Signal, SignalClassification, SignalSource
from apps.graph.models import Feature, WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.graph.services.graph_rag import explain_with_graph
from apps.projects.models import Issue, Project, Severity, WbsTask


class GraphRagTests(TestCase):
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
        self.other_project = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")

        self.feature = Feature.objects.create(project=self.project, name="受注登録")
        self.issue = Issue.objects.create(
            project=self.project,
            title="受注登録が失敗する",
            severity=Severity.HIGH,
            external_key="PRJ-123",
        )
        self.task = WbsTask.objects.create(project=self.project, wbs_code="3.2", name="結合試験")

        self.document = self._document(self.project, "受注登録 設計書")
        self.signal = self._signal(self.project, "受注登録の不具合報告", SignalSource.JIRA)

        self._link(self.issue, self.feature, RelationType.IMPACTS, Provenance.EXTERNAL_ID)
        self._link(self.feature, self.document, RelationType.EVIDENCED_BY, Provenance.MANUAL)

    # ── 補助 ─────────────────────────────────────────────
    def _document(self, project, title: str) -> Document:
        return Document.objects.create(
            tenant=project.tenant,
            project=project,
            title=title,
            file="dummy.pdf",
            file_type=FileType.PDF,
        )

    def _signal(self, project, summary: str, source: str, **extra) -> Signal:
        return Signal.objects.create(
            project=project,
            source=source,
            external_id=f"{source}-{summary}",
            classification=SignalClassification.DEFECT_REPORTED,
            occurred_at=extra.pop("occurred_at", timezone.now()),
            summary=summary,
            **extra,
        )

    def _link(self, source, target, relation, provenance, state=LinkState.CONFIRMED) -> WorkLink:
        link = WorkLink(
            relation_type=relation,
            from_object=source,
            to_object=target,
            provenance=provenance,
            state=state,
            source_reference="test",
            confirmed_by=self.user if state == LinkState.CONFIRMED else None,
            confirmed_at=timezone.now() if state == LinkState.CONFIRMED else None,
        )
        link.save()
        return link

    # ── 経路と説明 ────────────────────────────────────────
    def test_confirmed_path_returns_document_evidence(self):
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        self.assertFalse(result.is_empty)
        self.assertEqual({path.evidence.id for path in result.paths}, {self.document.pk})

    def test_path_edges_carry_provenance_and_confirmer(self):
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        path = result.paths[0]
        self.assertEqual(path.hops, 2)
        self.assertEqual([edge.provenance for edge in path.edges], ["external_id", "manual"])
        self.assertTrue(all(edge.confirmed_by == str(self.user) for edge in path.edges))
        self.assertTrue(all(edge.confirmed_at is not None for edge in path.edges))

    def test_explanation_mentions_relation_and_provenance(self):
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        text = result.paths[0].explain()
        self.assertIn("影響する", text)
        self.assertIn("手動登録", text)
        self.assertIn("受注登録 設計書", text)

    def test_wbs_code_is_used_as_origin(self):
        signal = self._signal(self.project, "結合試験が失敗", SignalSource.CI)
        self._link(self.task, signal, RelationType.EVIDENCED_BY, Provenance.MANUAL)
        result = explain_with_graph(self.project, "WBS 3.2 の遅延理由は?")
        self.assertEqual({path.evidence.id for path in result.paths}, {signal.pk})

    def test_feature_name_is_used_when_no_explicit_key(self):
        result = explain_with_graph(self.project, "受注登録について教えて")
        self.assertEqual({node.id for node in result.origins}, {self.feature.pk})
        self.assertEqual({path.evidence.id for path in result.paths}, {self.document.pk})

    # ── 候補・境界・鮮度 ───────────────────────────────────
    def test_candidate_evidence_link_is_excluded(self):
        candidate_doc = self._document(self.project, "AI が拾った議事録")
        self._link(
            self.feature,
            candidate_doc,
            RelationType.DISCUSSED_IN,
            Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
        )
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        self.assertNotIn(candidate_doc.pk, {path.evidence.id for path in result.paths})

    def test_candidate_intermediate_link_is_not_traversed(self):
        other_feature = Feature.objects.create(project=self.project, name="在庫引当")
        hidden = self._document(self.project, "在庫引当 設計書")
        self._link(
            self.issue,
            other_feature,
            RelationType.IMPACTS,
            Provenance.AI_CANDIDATE,
            state=LinkState.CANDIDATE,
        )
        self._link(other_feature, hidden, RelationType.EVIDENCED_BY, Provenance.MANUAL)
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        self.assertNotIn(hidden.pk, {path.evidence.id for path in result.paths})

    def test_other_project_evidence_is_not_mixed(self):
        other_feature = Feature.objects.create(project=self.other_project, name="受注登録")
        other_doc = self._document(self.other_project, "別案件の設計書")
        self._link(other_feature, other_doc, RelationType.EVIDENCED_BY, Provenance.MANUAL)
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        self.assertEqual({path.evidence.id for path in result.paths}, {self.document.pk})

    def test_other_tenant_origin_is_not_reachable(self):
        other_tenant = Tenant.objects.create(code="beta", name="BETA")
        other_project = Project.objects.create(tenant=other_tenant, code="p9", name="他社案件")
        Issue.objects.create(
            project=other_project,
            title="別テナントの課題",
            severity=Severity.HIGH,
            external_key="PRJ-123",
        )
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        self.assertEqual({node.id for node in result.origins}, {self.issue.pk})

    def test_revoked_signal_is_not_evidence(self):
        revoked = self._signal(
            self.project, "削除された投稿", SignalSource.SLACK, is_revoked=True
        )
        self._link(self.feature, revoked, RelationType.EVIDENCED_BY, Provenance.MANUAL)
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        self.assertNotIn(revoked.pk, {path.evidence.id for path in result.paths})

    def test_superseded_signal_is_not_evidence(self):
        original = self._signal(self.project, "誤報だった報告", SignalSource.SLACK)
        correction = self._signal(self.project, "訂正後の報告", SignalSource.SLACK)
        original.superseded_by = correction
        original.save()
        self._link(self.feature, original, RelationType.EVIDENCED_BY, Provenance.MANUAL)
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        self.assertNotIn(original.pk, {path.evidence.id for path in result.paths})

    def test_stale_signal_evidence_is_flagged(self):
        old = self._signal(
            self.project,
            "3日前のCI失敗",
            SignalSource.CI,
            occurred_at=timezone.now() - timedelta(days=3),
        )
        self._link(self.feature, old, RelationType.EVIDENCED_BY, Provenance.MANUAL)
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        stale = result.stale_paths
        self.assertEqual({path.evidence.id for path in stale}, {old.pk})
        self.assertTrue(result.freshness.is_degraded)
        self.assertIn("鮮度切れ", stale[0].explain())

    # ── ベクトル検索との比較 ────────────────────────────────
    def test_graph_only_and_vector_only_evidence_are_separated(self):
        vector_doc = self._document(self.project, "ベクトルだけで出た文書")
        result = explain_with_graph(
            self.project, "PRJ-123 の状況は?", vector_hits=[str(vector_doc.pk)]
        )
        self.assertEqual(result.graph_only_count, 1)
        self.assertEqual(result.vector_only_document_ids, (str(vector_doc.pk),))
        self.assertEqual({path.evidence.id for path in result.graph_only_paths}, {self.document.pk})

    def test_document_found_by_both_is_marked_as_overlapping(self):
        result = explain_with_graph(
            self.project, "PRJ-123 の状況は?", vector_hits=[self.document.pk]
        )
        self.assertEqual(result.graph_only_count, 0)
        self.assertEqual({path.evidence.id for path in result.overlapping_paths}, {self.document.pk})
        self.assertEqual(result.vector_only_document_ids, ())

    # ── 空・異常系 ────────────────────────────────────────
    def test_no_origin_returns_empty_without_error(self):
        result = explain_with_graph(self.project, "特に関係のない質問です")
        self.assertTrue(result.is_empty)
        self.assertEqual(result.origins, ())
        self.assertIsNotNone(result.freshness)
        self.assertIn("起点", result.describe())

    def test_blank_question_returns_empty(self):
        self.assertTrue(explain_with_graph(self.project, "").is_empty)

    def test_signal_evidence_reached_through_two_hops(self):
        self._link(self.feature, self.signal, RelationType.DISCUSSED_IN, Provenance.MANUAL)
        result = explain_with_graph(self.project, "PRJ-123 の状況は?")
        signal_paths = [p for p in result.paths if p.evidence.label == "forecast.signal"]
        self.assertEqual(len(signal_paths), 1)
        self.assertEqual(signal_paths[0].origin.id, self.issue.pk)
        self.assertEqual(signal_paths[0].hops, 2)
