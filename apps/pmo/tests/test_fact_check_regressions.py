"""事実照合の誤検出（正しい本文を「事実誤認あり」にする壊れ方）の回帰テスト。

誤検出は見逃しと同じくらい危険である。正しい計画ドラフトが必ず承認を止められる
状態が続けば、利用者はこの機能を「当てにならないもの」として無視するようになり、
本当の事実誤認も見つからなくなる。ここでは生成物をそのまま照合にかけて、
不一致が 1 件も出ないことを確かめる。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.agents.models import AgentRun, AgentStep, EvidenceEvaluation, Recommendation
from apps.pmo.models import Deliverable
from apps.pmo.services import fact_check, generators
from apps.projects.models import Issue, Milestone, Project, WbsTask

TODAY = date(2026, 7, 31)


class PlanDraftFactCheckTests(TestCase):
    """計画ドラフトを生成し、そのまま事実照合にかけたときの結果。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(
            tenant=self.tenant,
            code="p1",
            name="基幹刷新",
            project_manager="山田",
        )

        for code, name, offset in (
            ("1-1", "要件定義", -30),
            ("1-2", "基本設計", -3),
            ("2-1", "結合試験", 20),
        ):
            WbsTask.objects.create(
                project=self.project,
                wbs_code=code,
                name=name,
                owner="山田",
                planned_end=TODAY + timedelta(days=offset),
                progress_percent=Decimal("50"),
            )

        # 実績が入っていない（将来の）マイルストーン。
        Milestone.objects.create(
            project=self.project, name="設計完了", planned_date=TODAY + timedelta(days=120)
        )

    def _generated(self) -> Deliverable:
        result = generators.generate_and_save(
            project=self.project, generator_key="plan_draft", today=TODAY
        )

        return result.deliverable

    def test_計画ドラフトの事実照合は不一致0件になる(self):
        result = fact_check.check_deliverable(self._generated(), today=TODAY)

        self.assertEqual(result.mismatched_count, 0, [c.excerpt for c in result.mismatches])
        self.assertGreater(result.matched_count, 0)

    def test_案件名は括弧の手前までを案件名として読む(self):
        result = fact_check.check_deliverable(self._generated(), today=TODAY)
        names = [claim for claim in result.claims if claim.label == "案件名"]

        self.assertEqual([claim.written_value for claim in names], ["基幹刷新"])
        self.assertEqual([claim.verdict for claim in names], [fact_check.VERDICT_MATCH])

    def test_WBS10件のような件数をWBSコードとして拾わない(self):
        result = fact_check.check_deliverable(self._generated(), today=TODAY)

        self.assertEqual([claim for claim in result.claims if claim.label == "WBSコード"], [])

    def test_実績未のマイルストーンの予定日を実績日にしない(self):
        result = fact_check.check_deliverable(self._generated(), today=TODAY)

        self.assertEqual([claim for claim in result.claims if claim.label == "実績日"], [])

    def test_計画ドラフトは承認へ進める(self):
        deliverable = self._generated()
        deliverable.body = deliverable.ai_generated_body
        deliverable.save(update_fields=["body"])

        self.assertEqual(fact_check.mismatch_reason(deliverable), "")


class FactCheckPatternTests(TestCase):
    """個々の判定規則。生成物に依存しない形でも固定しておく。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        WbsTask.objects.create(project=self.project, wbs_code="1-1", name="要件定義", owner="山田")

    def _check(self, body: str, *, today: date | None = None) -> fact_check.FactCheckResult:
        deliverable = Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body=body,
        )

        return fact_check.check_deliverable(deliverable, today=today or TODAY)

    def test_区切りのあるWBSコードは従来どおり照合する(self):
        result = self._check("WBS: 9-9 が遅延しています。")

        self.assertEqual(result.mismatched_count, 1)
        self.assertEqual(result.mismatches[0].label, "WBSコード")

    def test_WBSコード表記も照合する(self):
        result = self._check("WBSコード 1-1 は完了。")

        self.assertEqual([claim.label for claim in result.claims], ["WBSコード"])
        self.assertEqual(result.matched_count, 1)

    def test_実績が未の行の日付は判定しない(self):
        future = TODAY + timedelta(days=60)
        result = self._check(f"{future.isoformat()}　受入試験（登録済み／実績 未）")

        self.assertEqual([claim for claim in result.claims if claim.kind == "date"], [])

    def test_実績として書かれた未来日は従来どおり検出する(self):
        future = TODAY + timedelta(days=60)
        result = self._check(f"実績: {future.isoformat()} に完了しました。")

        self.assertEqual(result.mismatched_count, 1)
        self.assertEqual(result.mismatches[0].label, "実績日")

    def test_実績日から離れた予定日は実績として扱わない(self):
        future = TODAY + timedelta(days=60)
        result = self._check(f"{future.isoformat()}　受入試験の予定（前回の実績 2026-01-10）")

        written = [claim.written_value for claim in result.claims if claim.kind == "date"]

        self.assertEqual(written, ["2026-01-10"])


class EvidenceBackedTests(TestCase):
    """「根拠あり」を、たまたま同じ数字があるだけで立てないこと。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

        for index in range(2):
            Issue.objects.create(project=self.project, title=f"課題{index}")

        self.run = AgentRun.objects.create(
            tenant=self.tenant,
            project=self.project,
            area=AgentRun.Area.DELIVERABLE,
            user_input="週次報告を作成して",
        )

    def _deliverable(self, body: str) -> Deliverable:
        return Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body=body,
            agent_run=self.run,
        )

    def _step(self, summary: str) -> None:
        AgentStep.objects.create(
            run=self.run, order=self.run.steps.count() + 1, tool_name="projects.Issue",
            output_summary=summary,
        )

    def test_数字が別の意味で出てくるだけでは裏付けにしない(self):
        # トレースにある「2」は日付の一部で、課題件数の根拠ではない。
        self._step("2026-07-31 時点の集計")

        result = fact_check.check_deliverable(self._deliverable("課題 2件。"), today=TODAY)

        self.assertEqual(result.unsupported_count, 1)

    def test_単位まで一致していれば裏付けありとする(self):
        self._step("status in (未対応/対応中) = 2件")

        result = fact_check.check_deliverable(self._deliverable("課題 2件。"), today=TODAY)

        self.assertEqual(result.unsupported_count, 0)

    def test_根拠評価の所見も根拠テキストとして読む(self):
        # 実体は `notes`。存在しないフィールドを読んでいると常に未裏付けになる。
        EvidenceEvaluation.objects.create(
            run=self.run,
            confidence=0.9,
            recommendation=Recommendation.ANSWER,
            notes="未解決の課題は 2件（登録データのみから集計）",
        )

        result = fact_check.check_deliverable(self._deliverable("課題 2件。"), today=TODAY)

        self.assertEqual(result.unsupported_count, 0)
