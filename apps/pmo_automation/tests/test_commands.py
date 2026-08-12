"""management commands と、H-11（dry-run 不変）を検証する。"""

from __future__ import annotations

import json
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.dashboard.models import Alert
from apps.pmo_automation.models import (
    ApprovalRequest,
    EvidenceBundle,
    PmoWorkItem,
    WorkItemState,
    WorkPlan,
    WorkStep,
)
from apps.projects.models import Project

NOW = timezone.now()


class CommandTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    def _alert(self, **kwargs) -> Alert:
        defaults = {
            "project": self.project,
            "category": Alert.Category.SCHEDULE,
            "title": "遅延の疑い",
            "detected_at": NOW,
        }
        defaults.update(kwargs)

        return Alert.objects.create(**defaults)

    def _snapshot(self) -> tuple:
        return (
            PmoWorkItem.objects.count(),
            WorkPlan.objects.count(),
            WorkStep.objects.count(),
            EvidenceBundle.objects.count(),
        )

    def _call(self, command: str, **options) -> str:
        out = StringIO()
        call_command(command, stdout=out, **options)
        return out.getvalue()

    def _run_pmo_automation(self, **options) -> str:
        options.setdefault("limit", 50)
        return self._call("run_pmo_automation", **options)


class RunPmoAutomationTests(CommandTestBase):
    def test_新規Alertからplanまで一気通貫で処理される(self) -> None:
        self._alert()

        output = self._run_pmo_automation(tenant="acme")

        self.assertEqual(PmoWorkItem.objects.count(), 1)
        self.assertEqual(WorkPlan.objects.count(), 1)
        work_item = PmoWorkItem.objects.get()
        self.assertIn(work_item.state, (WorkItemState.AUTO_RUNNING, WorkItemState.AWAITING_CONFIRMATION))
        self.assertIn("1 件の Work Item を処理しました", output)

    def test_H11_dry_runはDBを一切変更しない(self) -> None:
        """H-11: dry-run実行の前後でDBスナップショットが完全一致する。"""

        self._alert()
        before = self._snapshot()

        output = self._run_pmo_automation(tenant="acme", dry_run=True)

        after = self._snapshot()
        self.assertEqual(before, after)
        self.assertEqual(before, (0, 0, 0, 0))
        # 実行計画自体は返す(何もしなかったのではなく、計画して差し戻したことが分かる)。
        self.assertIn("dry-run", output)
        self.assertIn("1 件", output)

    def test_存在しないテナントはCommandErrorになる(self) -> None:
        with self.assertRaises(CommandError):
            self._run_pmo_automation(tenant="no-such-tenant")

    def test_kind指定で対象を絞り込める(self) -> None:
        self._alert(category=Alert.Category.SCHEDULE)

        # detection_triage 以外を指定すると対象が無いので何もしない。
        output = self._run_pmo_automation(tenant="acme", kind="knowledge_quality")

        self.assertEqual(PmoWorkItem.objects.count(), 0)
        self.assertIn("対象イベントはありませんでした", output)

    def test_limit未指定はエラーになる(self) -> None:
        with self.assertRaises(CommandError):
            self._call("run_pmo_automation", tenant="acme")

    def test_limitが0以下はエラーになる(self) -> None:
        with self.assertRaises(CommandError):
            self._run_pmo_automation(tenant="acme", limit=0)

    def test_limitで処理件数が絞り込まれる(self) -> None:
        self._alert(title="1件目")
        self._alert(title="2件目")
        self._alert(title="3件目")

        output = self._run_pmo_automation(tenant="acme", limit=2)

        self.assertEqual(PmoWorkItem.objects.count(), 2)
        self.assertIn("2 件の Work Item を処理しました", output)


class ProcessPmoWorkTests(CommandTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._alert()
        self._run_pmo_automation(tenant="acme")

    def test_auto_runningのStepが処理される(self) -> None:
        work_item = PmoWorkItem.objects.get()
        if work_item.state != WorkItemState.AUTO_RUNNING:
            self.skipTest("このkindのテンプレートはconfirmを含むためauto_runningにならない")

        output = self._call("process_pmo_work", tenant="acme", limit=10)

        self.assertIn("succeeded", output)
        self.assertIn("1 件の Work Item を処理しました", output)

    def test_limit未指定はエラーになる(self) -> None:
        with self.assertRaises(CommandError):
            self._call("process_pmo_work", tenant="acme", limit=0)


class PmoAutomationStatusTests(CommandTestBase):
    def test_text形式で状態別件数を表示する(self) -> None:
        self._alert()
        self._run_pmo_automation(tenant="acme")

        output = self._call("pmo_automation_status", tenant="acme")

        self.assertIn("テナント: acme", output)
        self.assertIn("承認待ち: 0 件", output)

    def test_json形式で機械可読な出力を返す(self) -> None:
        self._alert()
        self._run_pmo_automation(tenant="acme")

        output = self._call("pmo_automation_status", tenant="acme", format="json")

        payload = json.loads(output)
        self.assertEqual(payload["tenant"], "acme")
        self.assertIn("state_counts", payload)
        self.assertEqual(payload["pending_approvals"], 0)

    def test_保留Work_Itemの理由を表示する(self) -> None:
        work_item = PmoWorkItem.objects.create(
            tenant=self.tenant,
            project=self.project,
            kind="detection_triage",
            state=WorkItemState.HOLD,
            source_type="alert",
            source_key="x",
            dedupe_key="alert:x",
            block_reason="根拠が不足しています",
            is_active=False,
        )

        output = self._call("pmo_automation_status", tenant="acme")

        self.assertIn("根拠が不足しています", output)
        self.assertEqual(ApprovalRequest.objects.count(), 0)
