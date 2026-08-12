"""LDF-07: テスト管理・CI コネクタ契約と、Feature・不具合への結び付け。

受入条件「テスト失敗・修正・再試験の事実が Feature と不具合へ結び付く」。
契約違反を黙って既定値へ寄せないこと、候補を確定にしないことを固定する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.forecast.models import Signal, SignalClassification, TestEvidence
from apps.graph.models import Feature
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance
from apps.integrations.models import Connection, Provider
from apps.integrations.services.connectors.test_evidence import (
    ExternalTestResult,
    MockCiTestEvidenceConnector,
    TestEvidenceConnector,
)
from apps.integrations.services.test_evidence_bridge import ingest_test_results
from apps.projects.models import Issue, Project


class ConnectorContractTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.GIT,
            name="CI（読み取り専用）",
            base_url="https://example.invalid",
        )
        self.now = timezone.now()

    def test_mock_adapter_satisfies_the_contract(self):
        connector = MockCiTestEvidenceConnector(self.connection)
        self.assertIsInstance(connector, TestEvidenceConnector)
        self.assertTrue(connector.product_name)

    def test_results_are_deterministic(self):
        first = MockCiTestEvidenceConnector(self.connection, reference_time=self.now)
        second = MockCiTestEvidenceConnector(self.connection, reference_time=self.now)
        self.assertEqual(
            [r.external_id for r in first.fetch_results()],
            [r.external_id for r in second.fetch_results()],
        )

    def test_incremental_fetch_skips_older_results(self):
        connector = MockCiTestEvidenceConnector(self.connection, reference_time=self.now)
        recent = connector.fetch_results(since=self.now - timedelta(hours=2))
        self.assertEqual([r.external_id for r in recent], ["CI-103"])

    def test_unsupported_kind_is_rejected(self):
        result = ExternalTestResult(
            external_id="X-1",
            name="不明な種別",
            kind="smoke",
            result="passed",
            executed_at=self.now,
        )
        with self.assertRaises(ValueError):
            result.validate()

    def test_unsupported_result_is_rejected(self):
        result = ExternalTestResult(
            external_id="X-2",
            name="不明な結果",
            kind="unit",
            result="maybe",
            executed_at=self.now,
        )
        with self.assertRaises(ValueError):
            result.validate()


class TestEvidenceBridgeTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.feature = Feature.objects.create(project=self.project, name="受注登録")
        self.connection = Connection.objects.create(
            tenant=self.tenant,
            project=self.project,
            provider=Provider.GIT,
            name="CI",
            base_url="https://example.invalid",
        )
        self.now = timezone.now()
        self.connector = MockCiTestEvidenceConnector(self.connection, reference_time=self.now)

    def test_results_become_test_evidence(self):
        result = ingest_test_results(self.project, self.connector.fetch_results())
        self.assertEqual(result.created, 3)
        self.assertEqual(TestEvidence.objects.count(), 3)

    def test_failed_test_becomes_a_failure_signal(self):
        ingest_test_results(self.project, self.connector.fetch_results())
        failed = Signal.objects.filter(classification=SignalClassification.TEST_FAILED)
        self.assertTrue(failed.exists())
        self.assertTrue(failed.first().permalink.startswith("https://example.invalid/runs/"))

    def test_feature_link_is_a_candidate_not_confirmed(self):
        ingest_test_results(self.project, self.connector.fetch_results())
        links = WorkLink.objects.filter(from_object_id=self.feature.pk)
        self.assertTrue(links.exists())
        self.assertTrue(all(link.state == LinkState.CANDIDATE for link in links))
        self.assertTrue(all(link.provenance == Provenance.AI_CANDIDATE for link in links))

    def test_evidence_is_attached_to_the_feature(self):
        ingest_test_results(self.project, self.connector.fetch_results())
        evidence = TestEvidence.objects.get(external_id="CI-101")
        self.assertEqual(evidence.feature, self.feature)

    def test_defect_reference_creates_a_confirmed_link(self):
        Issue.objects.create(project=self.project, title="金額不一致", external_key="DEF-42")
        result = ingest_test_results(
            self.project,
            [
                ExternalTestResult(
                    external_id="CI-200",
                    name="再試験",
                    kind="system",
                    result="failed",
                    executed_at=self.now,
                    defect_reference="DEF-42",
                )
            ],
        )
        self.assertEqual(result.confirmed_links, 1)
        link = WorkLink.objects.filter(provenance=Provenance.EXTERNAL_ID).first()
        self.assertEqual(link.state, LinkState.CONFIRMED)

    def test_contract_violation_is_reported_not_defaulted(self):
        result = ingest_test_results(
            self.project,
            [
                ExternalTestResult(
                    external_id="CI-300",
                    name="壊れた結果",
                    kind="smoke",
                    result="passed",
                    executed_at=self.now,
                )
            ],
        )
        self.assertEqual(result.created, 0)
        self.assertEqual(len(result.rejected), 1)
        self.assertFalse(TestEvidence.objects.filter(external_id="CI-300").exists())

    def test_reingest_updates_instead_of_duplicating(self):
        ingest_test_results(self.project, self.connector.fetch_results())
        second = ingest_test_results(self.project, self.connector.fetch_results())
        self.assertEqual(second.updated, 3)
        self.assertEqual(TestEvidence.objects.count(), 3)

    def test_retest_plan_is_kept(self):
        ingest_test_results(
            self.project,
            [
                ExternalTestResult(
                    external_id="CI-400",
                    name="再試験予定あり",
                    kind="system",
                    result="failed",
                    executed_at=self.now,
                    retest_planned_on=timezone.localdate() + timedelta(days=2),
                )
            ],
        )
        evidence = TestEvidence.objects.get(external_id="CI-400")
        self.assertIsNotNone(evidence.retest_planned_on)
        self.assertFalse(evidence.blocks_completion)

    def test_summary_line_reports_the_breakdown(self):
        result = ingest_test_results(self.project, self.connector.fetch_results())
        self.assertIn("証跡 新規 3件", result.summary_line())

    def test_bridge_is_scoped_to_the_project(self):
        other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        ingest_test_results(self.project, self.connector.fetch_results())
        self.assertEqual(TestEvidence.objects.filter(project=other).count(), 0)
