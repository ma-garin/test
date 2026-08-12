"""services/report_cycle.py と、H-12（報告サイクル）を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo.models import Deliverable
from apps.pmo_automation.models import ApprovalRequest, EvidenceBundle, PmoWorkItem, WorkKind
from apps.pmo_automation.services import report_cycle
from apps.projects.models import Project, WbsTask

NOW = timezone.now()
TODAY = NOW.date()


class ReportCycleTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _work_item(self, **kwargs) -> PmoWorkItem:
        defaults = {
            "tenant": self.tenant,
            "project": self.project,
            "kind": WorkKind.REPORT_CYCLE,
            "source_type": "schedule",
            "source_key": "weekly",
            "dedupe_key": "schedule:weekly",
        }
        defaults.update(kwargs)

        return PmoWorkItem.objects.create(**defaults)

    def _seed_facts(self) -> None:
        """fact_checkが検証できる実データを最低限用意する。"""

        WbsTask.objects.create(
            project=self.project,
            wbs_code="T1",
            name="設計",
            status=WbsTask.Status.DONE,
            progress_percent=100,
            planned_end=TODAY - timedelta(days=10),
            actual_end=TODAY - timedelta(days=9),
        )


class RunReportCycleTests(ReportCycleTestBase):
    def test_未知のgenerator_keyはエラーになる(self) -> None:
        work_item = self._work_item()

        with self.assertRaises(report_cycle.ReportCycleError):
            report_cycle.run_report_cycle(work_item, generator_key="unknown", now=NOW)

    def test_前回承認済みが無くても新しい下書きを作れる(self) -> None:
        self._seed_facts()
        work_item = self._work_item()

        result = report_cycle.run_report_cycle(work_item, generator_key="weekly_report", now=NOW)

        self.assertEqual(result.deliverable.version, 1)
        self.assertEqual(result.deliverable.status, Deliverable.Status.DRAFT)
        self.assertIsNone(result.previous_deliverable)

    def test_H12_報告サイクルは新しい下書き差分事実チェック承認依頼を作る(self) -> None:
        """H-12: 締め時刻と対象データ、前回承認済み報告がある状態で report_cycle を
        実行すると、新しい下書き・差分・事実チェック・ApprovalRequestが作られる。
        承認済み本文は変更されない。"""

        self._seed_facts()
        previous = Deliverable.objects.create(
            project=self.project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title="旧タイトル",
            version=1,
            status=Deliverable.Status.APPROVED,
            body="旧本文（承認済み）",
        )
        work_item = self._work_item()

        result = report_cycle.run_report_cycle(work_item, generator_key="weekly_report", now=NOW)

        # 新しい下書き
        self.assertEqual(result.deliverable.version, 2)
        self.assertEqual(result.deliverable.status, Deliverable.Status.DRAFT)
        self.assertTrue(result.deliverable.ai_generated_body)

        # 差分
        self.assertIsNotNone(result.diff)

        # 事実チェック（本文の数値は facts.py と同じ集計方法で生成しているため一致するはず）
        self.assertGreater(result.fact_check.total, 0)
        self.assertTrue(result.fact_check.passed)

        # 事実チェック結果が EvidenceBundle として残る
        self.assertEqual(
            EvidenceBundle.objects.filter(work_item=work_item, source_type="fact_check").count(), 1
        )

        # 承認依頼が作られる
        self.assertEqual(ApprovalRequest.objects.count(), 1)
        self.assertEqual(result.approval.work_item_id, work_item.id)
        self.assertIn("差分", result.approval.diff_summary)

        # safety_assertion: 承認済み本文・版は一切変更されない
        previous.refresh_from_db()
        self.assertEqual(previous.body, "旧本文（承認済み）")
        self.assertEqual(previous.version, 1)
        self.assertEqual(previous.status, Deliverable.Status.APPROVED)
        self.assertEqual(result.previous_deliverable.pk, previous.pk)

    def test_下書きと承認依頼はそれぞれ別のWorkLinkでWork_Itemに紐付く(self) -> None:
        """WorkLinkは1レコード1ターゲットが設計上の前提のため、
        deliverable用とapproval用を別レコードに分けて作る。"""

        self._seed_facts()
        work_item = self._work_item()

        result = report_cycle.run_report_cycle(work_item, generator_key="weekly_report", now=NOW)

        self.assertEqual(work_item.links.filter(deliverable=result.deliverable, approval=None).count(), 1)
        self.assertEqual(work_item.links.filter(approval=result.approval, deliverable=None).count(), 1)
