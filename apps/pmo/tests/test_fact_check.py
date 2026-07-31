"""成果物本文の事実チェック（要件 #15）。

「生成直後は必ず通る」「人が数字を書き換えたら落ちる」の 2 つが本質。
ここが逆になると、チェックがあること自体が誤った安心になる。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo.models import Deliverable
from apps.pmo.services import fact_check
from apps.pmo.services.generators import build_document
from apps.projects.models import Issue, Priority, Project, Severity, WbsTask

TODAY = timezone.localdate()


class FactCheckTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新", progress_percent=50
        )

        for index in range(4):
            WbsTask.objects.create(
                project=self.project,
                wbs_code=f"1.{index}",
                name=f"タスク{index}",
                status=WbsTask.Status.DONE if index < 2 else WbsTask.Status.IN_PROGRESS,
                priority=Priority.MEDIUM,
                planned_start=TODAY - timedelta(days=10),
                planned_end=TODAY - timedelta(days=3) if index == 3 else TODAY + timedelta(days=5),
            )

        Issue.objects.create(
            project=self.project,
            title="未解決の課題",
            status=Issue.Status.OPEN,
            severity=Severity.HIGH,
            due_date=TODAY + timedelta(days=2),
        )

    def _deliverable(self, body: str) -> Deliverable:
        return Deliverable.objects.create(
            project=self.project, title="週次報告", ai_generated_body=body, body=body
        )

    def test_実データと一致する数値は検証済みになる(self) -> None:
        # タスク 4 件、完了 2 件、課題 1 件は DB と一致する。
        deliverable = self._deliverable("タスクは4件、うち完了は2件。課題は1件。")

        result = fact_check.check(deliverable)

        self.assertEqual(result.total, 3)
        self.assertTrue(result.passed)
        self.assertEqual(result.unverified_count, 0)

    def test_実データに無い数値を不一致として挙げる(self) -> None:
        deliverable = self._deliverable("タスクは99件です。")

        result = fact_check.check(deliverable)

        self.assertFalse(result.passed)
        self.assertEqual(result.unverified_count, 1)
        self.assertEqual(result.unverified[0].display, "99件")

    def test_不一致の記載箇所を残す(self) -> None:
        deliverable = self._deliverable("先週の状況\n未解決の課題は7件ある。")

        result = fact_check.check(deliverable)

        self.assertEqual(result.unverified[0].line, "未解決の課題は7件ある。")

    def test_単位のない数字は主張として拾わない(self) -> None:
        # WBS コードや版数を誤検知しないこと。
        deliverable = self._deliverable("WBS 6.2 の第3版について。")

        result = fact_check.check(deliverable)

        self.assertEqual(result.total, 0)

    def test_全角パーセントも同じ単位として扱う(self) -> None:
        deliverable = self._deliverable("進捗率は50％です。")

        result = fact_check.check(deliverable)

        self.assertTrue(result.passed)

    def test_本文が空なら検証しない(self) -> None:
        deliverable = self._deliverable("")

        result = fact_check.check(deliverable)

        self.assertEqual(result.total, 0)
        self.assertIn("本文がまだありません", result.summary)

    def test_確定本文を優先して検証する(self) -> None:
        """AI生成本文が正しくても、人が壊した確定本文を見逃さない。"""

        deliverable = Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            ai_generated_body="タスクは4件。",
            body="タスクは40件。",
        )

        result = fact_check.check(deliverable)

        self.assertFalse(result.passed)

    def test_生成直後の週次報告は必ず事実チェックを通る(self) -> None:
        """ジェネレータが出す数字は、この検証器が数え直した値と必ず一致すること。

        ここが落ちるなら、生成側と検証側で数え方がずれている。
        """

        document = build_document(self.project, "weekly_report")
        deliverable = self._deliverable(document.body)

        result = fact_check.check(deliverable)

        self.assertTrue(result.passed, result.summary)

    def test_生成直後の品質レポートも事実チェックを通る(self) -> None:
        document = build_document(self.project, "quality_report")
        deliverable = self._deliverable(document.body)

        result = fact_check.check(deliverable)

        self.assertTrue(result.passed, result.summary)
