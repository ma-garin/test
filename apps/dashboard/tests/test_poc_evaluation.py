"""PoC 受け入れ条件の合否判定テスト（要件 #50〜#54）。

このテストで一番守りたいのは「データが無いのに合格と出さない」こと。
インシデントの再発は、集計が出ているだけの状態を「達成」と読んだところから起きる。
そのため、判定不能のときに理由が必ず付くことを最初に確認する。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun, EvidenceEvaluation, Level, Recommendation
from apps.audit.models import Feedback
from apps.dashboard.models import Alert, KpiMeasurement
from apps.dashboard.services.poc_evaluation import (
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNKNOWN,
    build_poc_evaluation,
    business_days_between,
)
from apps.pmo.models import Deliverable
from apps.projects.models import Project

#: テスト中に目標値を固定する。既定値が変わってもテストの意図がぶれないようにする。
TEST_TARGETS = {
    "REPORT_HOURS_REDUCTION_PERCENT": 50,
    "CORRECTION_RATE_PERCENT": 20,
    "FACT_ERROR_COUNT": 0,
    "DETECTION_LEAD_BUSINESS_DAYS": 3,
}


class PocEvaluationTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="poc-user",
            email="poc-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")

    @property
    def projects(self):
        return Project.objects.filter(tenant=self.tenant)

    @property
    def feedbacks(self):
        return Feedback.objects.filter(tenant=self.tenant)

    def build(self):
        return build_poc_evaluation(self.projects, self.feedbacks)

    def criterion(self, key: str):
        report = self.build()

        return next(item for item in report.criteria if item.key == key)


@override_settings(POC_TARGETS=TEST_TARGETS)
class NoDataTests(PocEvaluationTestBase):
    """データが無いときに合否を名乗らないこと。"""

    def test_データが無いと全指標が判定不能になる(self):
        report = self.build()

        self.assertEqual(len(report.criteria), 5)
        self.assertEqual(report.unknown_count, 5)
        self.assertEqual(report.passed_count, 0)
        self.assertEqual(report.failed_count, 0)

    def test_判定不能には必ず理由が付く(self):
        for item in self.build().criteria:
            with self.subTest(criterion=item.key):
                self.assertEqual(item.verdict, VERDICT_UNKNOWN)
                self.assertTrue(item.reason.strip(), "判定不能なのに理由が空です")

    def test_総合判定も判定不能になる(self):
        report = self.build()

        self.assertEqual(report.overall_verdict, VERDICT_UNKNOWN)
        self.assertEqual(report.overall_label, "判定不能")


@override_settings(POC_TARGETS=TEST_TARGETS)
class CorrectionRateTests(PocEvaluationTestBase):
    """#51 赤字率。"""

    def _create_deliverable(self, ai_body: str, body: str) -> Deliverable:
        return Deliverable.objects.create(
            project=self.project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title="週次報告",
            ai_generated_body=ai_body,
            body=body,
        )

    def test_赤字率がSequenceMatcherの一致率から算出される(self):
        # 10 文字中 8 文字一致 → ratio 0.8 → 赤字率 20.0%
        self._create_deliverable("A" * 10, "A" * 8 + "BB")

        item = self.criterion("correction_rate")

        self.assertEqual(item.actual_value, 20.0)

    def test_目標と同値なら不合格になる(self):
        """「20% 未満」なので 20.0% はちょうど届いていない。"""

        self._create_deliverable("A" * 10, "A" * 8 + "BB")

        self.assertEqual(self.criterion("correction_rate").verdict, VERDICT_FAIL)

    def test_目標値を設定で緩めると合格に変わる(self):
        self._create_deliverable("A" * 10, "A" * 8 + "BB")

        with override_settings(POC_TARGETS={**TEST_TARGETS, "CORRECTION_RATE_PERCENT": 25}):
            item = self.criterion("correction_rate")

        self.assertEqual(item.verdict, VERDICT_PASS)
        self.assertIn("25%", item.target_text)

    def test_確定本文が無い成果物は母数から外して判定不能にする(self):
        self._create_deliverable("A" * 10, "")

        item = self.criterion("correction_rate")

        self.assertEqual(item.verdict, VERDICT_UNKNOWN)
        self.assertIn("確定本文", item.reason)


@override_settings(POC_TARGETS=TEST_TARGETS)
class DetectionLeadTests(PocEvaluationTestBase):
    """#53 予兆検知の先行性。"""

    def _create_alert(self, detected_at) -> Alert:
        return Alert.objects.create(
            project=self.project,
            category=Alert.Category.SCHEDULE,
            severity=Alert.Severity.WARNING,
            title="遅延の予兆",
            detected_at=detected_at,
        )

    def _create_weekly_report(self, created_at) -> Deliverable:
        report = Deliverable.objects.create(
            project=self.project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title="週次報告",
        )
        # created_at は auto_now_add のため、作成後に上書きする。
        Deliverable.objects.filter(pk=report.pk).update(created_at=created_at)

        return report

    def test_比較対象の報告が無ければ判定不能になる(self):
        self._create_alert(timezone.now())

        item = self.criterion("detection_lead")

        self.assertEqual(item.verdict, VERDICT_UNKNOWN)
        self.assertIn("比較対象の報告がありません", item.reason)

    def test_アラートが無ければ判定不能になる(self):
        self._create_weekly_report(timezone.now())

        item = self.criterion("detection_lead")

        self.assertEqual(item.verdict, VERDICT_UNKNOWN)
        self.assertIn("アラート", item.reason)

    def test_先行日数は土日を除いて数える(self):
        friday = timezone.make_aware(datetime(2026, 7, 31, 9, 0))
        next_wednesday = friday + timedelta(days=5)

        self._create_alert(friday)
        self._create_weekly_report(next_wednesday)

        item = self.criterion("detection_lead")

        self.assertEqual(item.actual_value, 3.0)
        self.assertEqual(item.verdict, VERDICT_PASS)

    def test_祝日を考慮していないことを明示する(self):
        friday = timezone.make_aware(datetime(2026, 7, 31, 9, 0))
        self._create_alert(friday)
        self._create_weekly_report(friday + timedelta(days=5))

        self.assertTrue(any("祝日" in note for note in self.criterion("detection_lead").notes))

    def test_営業日計算は逆順なら負になる(self):
        self.assertEqual(business_days_between(datetime(2026, 7, 31).date(), datetime(2026, 8, 5).date()), 3)
        self.assertEqual(business_days_between(datetime(2026, 8, 5).date(), datetime(2026, 7, 31).date()), -3)


@override_settings(POC_TARGETS=TEST_TARGETS)
class ReportHoursAndFactErrorTests(PocEvaluationTestBase):
    """#50 レポート作業時間 と #52 事実誤認。"""

    def test_基準値が無ければ削減率を判定しない(self):
        KpiMeasurement.objects.create(
            project=self.project,
            kind=KpiMeasurement.Kind.REPORT_HOURS,
            measured_on=timezone.localdate(),
            actual_value=Decimal("4"),
            unit="時間",
        )

        item = self.criterion("report_hours")

        self.assertEqual(item.verdict, VERDICT_UNKNOWN)
        self.assertIn("基準値", item.reason)

    def test_削減率が目標に届けば合格になる(self):
        KpiMeasurement.objects.create(
            project=self.project,
            kind=KpiMeasurement.Kind.REPORT_HOURS,
            measured_on=timezone.localdate(),
            baseline_value=Decimal("10"),
            actual_value=Decimal("4"),
            unit="時間",
        )

        item = self.criterion("report_hours")

        self.assertEqual(item.actual_value, 60.0)
        self.assertEqual(item.verdict, VERDICT_PASS)

    def test_目標値を厳しくすると不合格に変わる(self):
        KpiMeasurement.objects.create(
            project=self.project,
            kind=KpiMeasurement.Kind.REPORT_HOURS,
            measured_on=timezone.localdate(),
            baseline_value=Decimal("10"),
            actual_value=Decimal("4"),
            unit="時間",
        )

        with override_settings(POC_TARGETS={**TEST_TARGETS, "REPORT_HOURS_REDUCTION_PERCENT": 70}):
            self.assertEqual(self.criterion("report_hours").verdict, VERDICT_FAIL)

    def _create_feedback(self, has_fact_error: bool) -> Feedback:
        return Feedback.objects.create(
            tenant=self.tenant,
            user=self.user,
            rating=Feedback.Rating.GOOD,
            has_fact_error=has_fact_error,
        )

    def test_事実誤認が無ければ合格になる(self):
        self._create_feedback(False)

        self.assertEqual(self.criterion("fact_error").verdict, VERDICT_PASS)

    def test_事実誤認が1件でもあれば不合格になる(self):
        self._create_feedback(True)

        item = self.criterion("fact_error")

        self.assertEqual(item.verdict, VERDICT_FAIL)
        self.assertEqual(item.actual_value, 1.0)


@override_settings(POC_TARGETS=TEST_TARGETS)
class HitlBlockTests(PocEvaluationTestBase):
    """#54 HITL 承認前ブロックの実演。"""

    def _create_blocked_deliverable(self) -> Deliverable:
        run = AgentRun.objects.create(
            tenant=self.tenant,
            project=self.project,
            area=AgentRun.Area.DELIVERABLE,
            status=AgentRun.Status.SUCCEEDED,
        )
        EvidenceEvaluation.objects.create(
            run=run,
            confidence=0.2,
            relevance=Level.LOW,
            coverage=Level.LOW,
            recommendation=Recommendation.ASK_CLARIFICATION,
            missing_information=["進捗実績の一次データ"],
        )

        return Deliverable.objects.create(
            project=self.project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title="根拠不足の週次報告",
            status=Deliverable.Status.DRAFT,
            agent_run=run,
        )

    def test_ブロック対象が無ければ実演できないので判定不能(self):
        item = self.criterion("hitl_block")

        self.assertEqual(item.verdict, VERDICT_UNKNOWN)
        self.assertIn("実演できません", item.reason)

    def test_ブロック対象があれば合格になり理由が付く(self):
        self._create_blocked_deliverable()
        report = self.build()
        item = next(row for row in report.criteria if row.key == "hitl_block")

        self.assertEqual(item.verdict, VERDICT_PASS)
        self.assertEqual(len(report.blocked_deliverables), 1)
        self.assertIn("進捗実績の一次データ", report.blocked_deliverables[0].reason)

    def test_根拠が十分な成果物はブロック対象に入らない(self):
        run = AgentRun.objects.create(
            tenant=self.tenant,
            project=self.project,
            area=AgentRun.Area.DELIVERABLE,
            status=AgentRun.Status.SUCCEEDED,
        )
        EvidenceEvaluation.objects.create(
            run=run,
            confidence=0.9,
            relevance=Level.HIGH,
            coverage=Level.HIGH,
            recommendation=Recommendation.ANSWER,
        )
        Deliverable.objects.create(
            project=self.project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title="根拠十分な週次報告",
            status=Deliverable.Status.DRAFT,
            agent_run=run,
        )

        self.assertEqual(len(self.build().blocked_deliverables), 0)


@override_settings(POC_TARGETS=TEST_TARGETS)
class PocScreenTests(PocEvaluationTestBase):
    """画面が表示され、判定結果と前提が本文に出ること。"""

    def test_画面が表示され判定不能の理由が出る(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard:poc"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "判定不能")
        self.assertContains(response, "祝日は考慮していません")
        self.assertContains(response, "POC_TARGETS")

    def test_テナント未選択でも壊れない(self):
        stranger = User.objects.create_user(
            username="no-tenant",
            email="no-tenant@example.com",
            password="test-password",
            tenant=None,
            role=Role.VIEWER,
        )
        self.client.force_login(stranger)

        response = self.client.get(reverse("dashboard:poc"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["report"].unknown_count, 5)

    def test_他テナントのデータが判定に混ざらない(self):
        other = Tenant.objects.create(code="globex", name="Globex")
        other_project = Project.objects.create(tenant=other, code="gx", name="他社案件")
        Deliverable.objects.create(
            project=other_project,
            kind=Deliverable.Kind.WEEKLY_REPORT,
            title="他社の週次報告",
            ai_generated_body="A" * 10,
            body="A" * 10,
        )
        Feedback.objects.create(tenant=other, rating=Feedback.Rating.GOOD, has_fact_error=True)

        report = self.build()

        self.assertEqual(report.unknown_count, 5)
