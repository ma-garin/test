"""承認ゲートと赤字率のテスト。

「AI が作成した提案・報告は、人が編集・承認するまでは確定情報として扱わない」
「根拠不足時は承認前にブロックする」という要件の実装を検証する。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.agents.models import AgentRun, EvidenceEvaluation, Recommendation
from apps.pmo.models import Deliverable
from apps.projects.models import Project


class ApprovalGateTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.run = AgentRun.objects.create(
            tenant=self.tenant,
            area=AgentRun.Area.DELIVERABLE,
            user_input="週次報告を作成して",
        )

    def _deliverable(self, **kwargs) -> Deliverable:
        return Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            **kwargs,
        )

    def test_根拠不足なら承認申請できない(self):
        EvidenceEvaluation.objects.create(
            run=self.run,
            confidence=0.2,
            recommendation=Recommendation.ASK_CLARIFICATION,
        )

        self.assertFalse(self._deliverable(agent_run=self.run).can_request_approval)

    def test_根拠に矛盾がある場合も承認申請できない(self):
        EvidenceEvaluation.objects.create(
            run=self.run,
            confidence=0.9,
            recommendation=Recommendation.ANSWER,
            has_conflict=True,
        )

        self.assertFalse(self._deliverable(agent_run=self.run).can_request_approval)

    def test_根拠が十分なら承認申請できる(self):
        EvidenceEvaluation.objects.create(
            run=self.run,
            confidence=0.8,
            recommendation=Recommendation.ANSWER,
        )

        self.assertTrue(self._deliverable(agent_run=self.run).can_request_approval)

    def test_AI不使用の成果物は承認申請できる(self):
        # AI 未設定でも画面と業務が止まらないこと。
        self.assertTrue(self._deliverable().can_request_approval)


class CorrectionRateTests(TestCase):
    def setUp(self) -> None:
        tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=tenant, code="p1", name="案件1")

    def _deliverable(self, ai_body: str, body: str) -> Deliverable:
        return Deliverable(project=self.project, title="週次報告", ai_generated_body=ai_body, body=body)

    def test_無編集なら赤字率は0(self):
        text = "今週は結合試験を実施しました。"

        self.assertEqual(self._deliverable(text, text).correction_rate, 0.0)

    def test_大幅に書き換えると赤字率が上がる(self):
        deliverable = self._deliverable(
            "今週は結合試験を実施しました。",
            "全く異なる内容へ差し替えた本文",
        )

        self.assertGreater(deliverable.correction_rate, 0.5)

    def test_AI生成本文がなければ算出しない(self):
        self.assertIsNone(self._deliverable("", "人が書いた本文").correction_rate)
