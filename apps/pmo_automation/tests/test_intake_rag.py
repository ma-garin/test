"""services/intake_rag.py（PA-12: RAG品質修復）を検証する。"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.pmo_automation.models import PmoWorkItem, WorkKind
from apps.pmo_automation.services import intake_rag
from apps.projects.models import Project
from apps.rag.models import EvaluationRun, EvaluationSuite


class IntakeFromRagEvaluationTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _run(self, **kwargs) -> EvaluationRun:
        defaults = {
            "tenant": self.tenant,
            "project": self.project,
            "suite": EvaluationSuite.RETRIEVAL,
            "evaluable": False,
            "unavailable_reason": "Goldenデータセットが0件のため評価不能。",
        }
        defaults.update(kwargs)

        return EvaluationRun.objects.create(**defaults)

    def test_評価不能な実行はknowledge_quality_Work_Itemを作る(self) -> None:
        run = self._run()

        result = intake_rag.intake_from_rag_evaluation_degradation(run)

        self.assertTrue(result.created)
        work_item = result.work_item
        self.assertEqual(work_item.kind, WorkKind.KNOWLEDGE_QUALITY)
        self.assertEqual(work_item.block_reason, "Goldenデータセットが0件のため評価不能。")
        self.assertEqual(PmoWorkItem.objects.count(), 1)

    def test_評価可能な実行は対象外(self) -> None:
        run = self._run(evaluable=True, unavailable_reason="")

        with self.assertRaises(intake_rag.RagIntakeError):
            intake_rag.intake_from_rag_evaluation_degradation(run)

        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_案件未指定の評価実行は対象外(self) -> None:
        run = self._run(project=None)

        with self.assertRaises(intake_rag.RagIntakeError):
            intake_rag.intake_from_rag_evaluation_degradation(run)

        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_dry_runは新規作成をDBに保存しない(self) -> None:
        run = self._run()

        result = intake_rag.intake_from_rag_evaluation_degradation(run, dry_run=True)

        self.assertTrue(result.created)
        self.assertIsNone(result.work_item)
        self.assertEqual(PmoWorkItem.objects.count(), 0)

    def test_同じ評価実行を再度intakeしてもWork_Itemは増えない(self) -> None:
        run = self._run()

        first = intake_rag.intake_from_rag_evaluation_degradation(run)
        second = intake_rag.intake_from_rag_evaluation_degradation(run)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.work_item.pk, first.work_item.pk)
        self.assertEqual(PmoWorkItem.objects.count(), 1)

    def test_dedupe_keyは決定的に導かれる(self) -> None:
        run = self._run()
        expected = f"rag_evaluation:{run.pk}"

        result = intake_rag.intake_from_rag_evaluation_degradation(run)

        self.assertEqual(result.dedupe_key, expected)
        self.assertEqual(result.work_item.source_type, "rag_evaluation")
        self.assertEqual(result.work_item.source_key, str(run.pk))
