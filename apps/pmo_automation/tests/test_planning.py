"""services/planning.py と、H-04（根拠競合）を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_automation.models import (
    AutomationLevel,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkPlan,
)
from apps.pmo_automation.services import planning
from apps.projects.models import Project

NOW = timezone.now()


class PlanningTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _work_item(self, **kwargs) -> PmoWorkItem:
        defaults = {
            "tenant": self.tenant,
            "project": self.project,
            "kind": WorkKind.DETECTION_TRIAGE,
            "source_type": "alert",
            "source_key": "alert-1",
            "dedupe_key": "alert:alert-1",
            "state": WorkItemState.ASSESSING,
        }
        defaults.update(kwargs)

        return PmoWorkItem.objects.create(**defaults)

    def _evidence(self, work_item, **kwargs):
        defaults = {
            "source_type": "alert",
            "source_ref": "alert-1",
            "scope": {"tenant": self.tenant.code, "project": self.project.code},
            "content_hash": "hash-1",
            "captured_at": NOW,
            "expires_at": NOW + timedelta(hours=1),
        }
        defaults.update(kwargs)

        return planning.record_evidence(work_item, **defaults)


class RecordEvidenceTests(PlanningTestBase):
    def test_必須項目が揃えば根拠を保存できる(self) -> None:
        work_item = self._work_item()

        evidence = self._evidence(work_item)

        self.assertEqual(evidence.work_item_id, work_item.id)

    def test_source_refが無ければ保存できない(self) -> None:
        work_item = self._work_item()

        with self.assertRaises(planning.EvidenceError):
            self._evidence(work_item, source_ref="")

    def test_scopeが無ければ保存できない(self) -> None:
        work_item = self._work_item()

        with self.assertRaises(planning.EvidenceError):
            self._evidence(work_item, scope={})

    def test_content_hashが無ければ保存できない(self) -> None:
        work_item = self._work_item()

        with self.assertRaises(planning.EvidenceError):
            self._evidence(work_item, content_hash="")


class BuildPlanTests(PlanningTestBase):
    def test_全P0種別でLLM無しにplanを作れる(self) -> None:
        for kind in WorkKind.values:
            with self.subTest(kind=kind):
                work_item = self._work_item(kind=kind, dedupe_key=f"alert:{kind}")

                plan = planning.build_plan(work_item)

                self.assertEqual(plan.version, 1)
                self.assertGreaterEqual(plan.steps.count(), 1)

    def test_未知のkindはValueErrorになる(self) -> None:
        work_item = self._work_item()
        work_item.kind = "unknown_kind"

        with self.assertRaises(ValueError):
            planning.build_plan(work_item)

    def test_2回目のbuild_planはversionが2になる(self) -> None:
        work_item = self._work_item()
        planning.build_plan(work_item)

        second = planning.build_plan(work_item)

        self.assertEqual(second.version, 2)
        self.assertEqual(WorkPlan.objects.filter(work_item=work_item).count(), 2)

    def test_idempotency_keyはplan版とstep順から決定的に導かれる(self) -> None:
        work_item = self._work_item()

        plan = planning.build_plan(work_item)

        step = plan.steps.get(order=1)
        self.assertEqual(step.idempotency_key, f"{work_item.dedupe_key}:{plan.version}:1")


class CreatePlanAndEvaluateTests(PlanningTestBase):
    def test_根拠が新鮮ならauto_runningまで進む(self) -> None:
        work_item = self._work_item()
        evidence = [self._evidence(work_item)]

        planning.create_plan_and_evaluate(work_item, evidence_bundles=evidence, now=NOW)

        work_item.refresh_from_db()
        self.assertEqual(work_item.state, WorkItemState.AUTO_RUNNING)

    def test_H04_根拠が競合すると確認待ちで止まる(self) -> None:
        """H-04: 同じconflict_groupに相反する根拠がある状態でPlanを作成して評価すると、
        Work Itemはawaiting_confirmationになり、completedにはならない。
        競合するsource_refは両方EvidenceBundleとして残り、承認パケットで両方参照できる。"""

        work_item = self._work_item()
        evidence = [
            self._evidence(work_item, source_ref="賛成の根拠", conflict_group="g1"),
            self._evidence(work_item, source_ref="反対の根拠", conflict_group="g1"),
        ]

        plan = planning.create_plan_and_evaluate(work_item, evidence_bundles=evidence, now=NOW)

        work_item.refresh_from_db()
        self.assertEqual(work_item.state, WorkItemState.AWAITING_CONFIRMATION)
        self.assertNotEqual(work_item.state, WorkItemState.COMPLETED)
        self.assertNotEqual(plan.automation_level, None)
        conflicting_refs = set(
            work_item.evidence_bundles.filter(conflict_group="g1").values_list("source_ref", flat=True)
        )
        self.assertEqual(conflicting_refs, {"賛成の根拠", "反対の根拠"})

    def test_confirmを含むkindは根拠が新鮮でも確認待ちになる(self) -> None:
        work_item = self._work_item(
            kind=WorkKind.DATA_QUALITY_REPAIR, dedupe_key="alert:data_quality_repair"
        )
        evidence = [self._evidence(work_item)]

        planning.create_plan_and_evaluate(work_item, evidence_bundles=evidence, now=NOW)

        work_item.refresh_from_db()
        self.assertEqual(work_item.state, WorkItemState.AWAITING_CONFIRMATION)
