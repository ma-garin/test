"""事実誤認の自動チェックのテスト。

PoC 評価指標「事実誤認 0 件」を人の目視に頼らず測るための機能なので、
**「照合できなかった」が「一致」に混ざらないこと**を最重要の検証項目に置く。
検査できていないものを問題なしと報告するのが、この機能で最も危険な壊れ方であるため。
"""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.audit.models import Feedback
from apps.dashboard.services.poc_evaluation import (
    VERDICT_FAIL,
    VERDICT_UNKNOWN,
    build_poc_evaluation,
)
from apps.pmo.models import Approval, Deliverable
from apps.pmo.services import approval as approval_service
from apps.pmo.services import deliverables as deliverable_service
from apps.pmo.services import fact_check
from apps.projects.models import Issue, Project, WbsTask


class FactCheckTests(TestCase):
    """本文と実データの突き合わせ。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新", project_manager="山田"
        )
        # タスク 3件（完了 1件）／課題 2件。ここが照合の正解値になる。
        for index in range(3):
            WbsTask.objects.create(
                project=self.project,
                wbs_code=f"1.{index}",
                name=f"タスク{index}",
                owner="山田",
                status=WbsTask.Status.DONE if index == 0 else WbsTask.Status.IN_PROGRESS,
            )

        for index in range(2):
            Issue.objects.create(project=self.project, title=f"課題{index}")

    def _deliverable(self, body: str, *, project: Project | None = None) -> Deliverable:
        return Deliverable.objects.create(
            project=project or self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body=body,
        )

    def test_実データと同じ数値は一致になる(self):
        result = fact_check.check_deliverable(
            self._deliverable("タスク 3件、完了 1件。\n課題 2件。")
        )

        self.assertEqual(result.matched_count, 3)
        self.assertEqual(result.mismatched_count, 0)
        self.assertEqual(result.unknown_count, 0)

    def test_数値を書き換えると不一致として検出される(self):
        result = fact_check.check_deliverable(self._deliverable("課題 7件を管理中です。"))

        self.assertEqual(result.mismatched_count, 1)
        self.assertEqual(result.matched_count, 0)
        claim = result.mismatches[0]
        self.assertEqual(claim.written_value, "7件")
        self.assertEqual(claim.expected_value, "2件")
        self.assertEqual(claim.excerpt, "課題 7件を管理中です。")
        self.assertEqual(claim.line_number, 1)

    def test_照合できない記述は不明として数え一致に含めない(self):
        result = fact_check.check_deliverable(self._deliverable("残業 12件が発生しました。"))

        self.assertEqual(result.checked_count, 1)
        self.assertEqual(result.unknown_count, 1)
        self.assertEqual(result.matched_count, 0)
        self.assertEqual(result.mismatched_count, 0)
        self.assertFalse(result.blocks_approval)

    def test_実データが無い案件では不一致にせず照合不能とする(self):
        # データ未取込と捏造を機械的に区別できないため、断定してはいけない。
        empty = Project.objects.create(tenant=self.tenant, code="p2", name="未取込案件")
        result = fact_check.check_deliverable(
            self._deliverable("課題 7件。", project=empty)
        )

        self.assertEqual(result.unknown_count, 1)
        self.assertEqual(result.mismatched_count, 0)

    def test_数値を含まない本文では誤検出しない(self):
        result = fact_check.check_deliverable(
            self._deliverable("今週は結合試験を進めました。特筆事項はありません。")
        )

        self.assertEqual(result.checked_count, 0)
        self.assertEqual(result.mismatched_count, 0)
        self.assertFalse(result.blocks_approval)
        self.assertIn("照合できる数値", result.summary)

    def test_確定本文があれば確定本文を検査する(self):
        deliverable = self._deliverable("課題 2件。")
        deliverable.body = "課題 9件。"
        deliverable.save(update_fields=["body"])

        result = fact_check.check_deliverable(deliverable)

        self.assertEqual(result.body_source, "確定本文")
        self.assertEqual(result.mismatched_count, 1)

    def test_実在しない担当者とWBSコードを検出する(self):
        result = fact_check.check_deliverable(
            self._deliverable("担当: 佐藤\nWBS: 9.9 が遅延しています。")
        )

        self.assertEqual(result.mismatched_count, 2)
        self.assertEqual({claim.label for claim in result.mismatches}, {"担当者", "WBSコード"})

    def test_実在する担当者とWBSコードは一致になる(self):
        result = fact_check.check_deliverable(self._deliverable("担当: 山田\nWBS: 1.1"))

        self.assertEqual(result.matched_count, 2)
        self.assertEqual(result.mismatched_count, 0)

    def test_案件名が違えば不一致になる(self):
        result = fact_check.check_deliverable(self._deliverable("案件: 別の案件"))

        self.assertEqual(result.mismatched_count, 1)
        self.assertEqual(result.mismatches[0].expected_value, "基幹刷新")

    def test_未来の日付を実績として書くと不一致になる(self):
        future = timezone.localdate() + timedelta(days=30)
        body = f"実績: {future.year}年{future.month}月{future.day}日に完了しました。"

        result = fact_check.check_deliverable(self._deliverable(body))

        self.assertEqual(result.mismatched_count, 1)
        self.assertEqual(result.mismatches[0].label, "実績日")

    def test_予定日は未来でも不一致にしない(self):
        future = timezone.localdate() + timedelta(days=30)
        body = f"次回リリース予定は {future.year}年{future.month}月{future.day}日です。"

        result = fact_check.check_deliverable(self._deliverable(body))

        self.assertEqual(result.checked_count, 0)

    def test_過去の実績日は一致になる(self):
        past = date(2020, 1, 15)
        result = fact_check.check_deliverable(
            self._deliverable(f"実績: {past.year}年{past.month}月{past.day}日に完了しました。")
        )

        self.assertEqual(result.matched_count, 1)

    def test_根拠が無い数値を未裏付けとして数える(self):
        result = fact_check.check_deliverable(self._deliverable("課題 2件。"))

        self.assertEqual(result.unsupported_count, 1)


class FactCheckApprovalGateTests(TestCase):
    """不一致がある成果物を承認へ進ませないこと。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        Issue.objects.create(project=self.project, title="課題1")

    def _deliverable(self, body: str) -> Deliverable:
        return Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body=body,
        )

    def test_不一致があると承認申請を拒否する(self):
        deliverable = self._deliverable("課題 5件。")

        result = approval_service.decide(
            deliverable=deliverable, actor=None, decision=Approval.Decision.REQUESTED
        )

        self.assertFalse(result.ok)
        self.assertIn("実データと一致しない記述", result.message)
        deliverable.refresh_from_db()
        self.assertEqual(deliverable.status, Deliverable.Status.DRAFT)
        self.assertEqual(Approval.objects.count(), 0)

    def test_一致していれば承認申請できる(self):
        deliverable = self._deliverable("課題 1件。")

        result = approval_service.decide(
            deliverable=deliverable, actor=None, decision=Approval.Decision.REQUESTED
        )

        self.assertTrue(result.ok)
        deliverable.refresh_from_db()
        self.assertEqual(deliverable.status, Deliverable.Status.PENDING_APPROVAL)

    def test_照合不能だけならブロックしない(self):
        deliverable = self._deliverable("残業 12件。")

        self.assertEqual(approval_service.blocking_reason(deliverable), "")

    def test_一覧の行にも事実照合の結果が乗る(self):
        deliverable = self._deliverable("課題 5件。")
        report = deliverable_service.build_report([deliverable])
        row = report.rows[0]

        self.assertFalse(row.can_approve)
        self.assertEqual(row.fact_result.mismatched_count, 1)
        self.assertEqual(report.blocked_count, 1)


class FactCheckScreenTests(TestCase):
    """成果物支援画面の「事実確認」導線。"""

    def setUp(self) -> None:
        from apps.accounts.constants import Role
        from apps.accounts.models import User

        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-user",
            email="pmo-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        Issue.objects.create(project=self.project, title="課題1")
        self.deliverable = Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body="課題 5件。",
        )
        self.client.force_login(self.user)

    def test_事実確認の明細を開ける(self):
        from django.urls import reverse

        url = reverse("pmo:deliverables")
        response = self.client.get(
            url, {"deliverable": str(self.deliverable.pk), "factcheck": "1"}
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("事実確認", body)
        self.assertIn("照合できなかった", body)
        self.assertIn("承認へ進めません", body)


class PocFactErrorCriterionTests(TestCase):
    """PoC 評価指標「事実誤認 0 件」を自動照合から算出すること。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        Issue.objects.create(project=self.project, title="課題1")
        self.projects = Project.objects.filter(tenant=self.tenant)
        self.feedbacks = Feedback.objects.filter(tenant=self.tenant)

    def _criterion(self):
        report = build_poc_evaluation(self.projects, self.feedbacks)

        return next(item for item in report.criteria if item.key == "fact_error")

    def test_フィードバックも照合対象も無ければ判定不能(self):
        self.assertEqual(self._criterion().verdict, VERDICT_UNKNOWN)

    def test_自動照合の不一致が事実誤認として数えられる(self):
        Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body="課題 5件。",
        )

        criterion = self._criterion()

        self.assertEqual(criterion.verdict, VERDICT_FAIL)
        self.assertEqual(criterion.actual_value, 1.0)
        self.assertIn("自動照合", criterion.baseline_text)

    def test_照合不能な記述は誤認に数えず理由に明記する(self):
        Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body="残業 12件。",
        )

        criterion = self._criterion()

        self.assertEqual(criterion.actual_value, 0.0)
        self.assertIn("照合できなかった記述が 1 件", criterion.reason)
