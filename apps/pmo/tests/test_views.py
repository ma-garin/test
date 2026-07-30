"""PMO 支援画面の疎通と、承認 POST の挙動テスト。

「根拠不足の成果物は承認できない」ことは画面の表示だけでなく、POST を直接
叩かれても通らないことまで確認する。ボタンの disabled は防御にならないため。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun, EvidenceEvaluation, Recommendation
from apps.pmo.models import Approval, Deliverable, PlanDraft, PromptTemplate
from apps.projects.models import Project

SCREENS = [
    "pmo:planning",
    "pmo:deliverables",
    "pmo:approvals",
    "pmo:prompt_library",
    "pmo:education",
]


class PmoScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-user",
            email="pmo-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.client.force_login(self.user)

    def _run(self) -> AgentRun:
        return AgentRun.objects.create(
            tenant=self.tenant, area=AgentRun.Area.DELIVERABLE, user_input="週次報告を作成して"
        )

    def _deliverable(self, **kwargs) -> Deliverable:
        defaults = {
            "project": self.project,
            "title": "週次報告",
            "kind": Deliverable.Kind.WEEKLY_REPORT,
            "ai_generated_body": "今週は結合試験を実施しました。",
            "body": "今週は結合試験を実施しました。",
        }

        return Deliverable.objects.create(**{**defaults, **kwargs})

    def test_全画面が実データで200を返す(self):
        PlanDraft.objects.create(
            project=self.project,
            title="移行計画",
            body="移行手順の下書き",
            review_points=["切り戻し手順", "停止時間"],
        )
        self._deliverable()

        for name in SCREENS:
            with self.subTest(screen=name):
                response = self.client.get(reverse(name))

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "この画面はまだ移植されていません")

    def test_計画画面にドラフトとレビュー観点が出る(self):
        PlanDraft.objects.create(
            project=self.project, title="移行計画", review_points=["切り戻し手順"]
        )
        response = self.client.get(reverse("pmo:planning"))

        self.assertContains(response, "移行計画")
        self.assertContains(response, "切り戻し手順")

    def test_成果物画面に赤字率が出る(self):
        self._deliverable(ai_generated_body="AIの下書き", body="全く異なる確定本文へ差し替えた")
        response = self.client.get(reverse("pmo:deliverables"))

        self.assertContains(response, "赤字率")
        self.assertContains(response, "%")

    def test_テンプレート未登録なら既定セットを出す(self):
        response = self.client.get(reverse("pmo:prompt_library"))

        self.assertContains(response, "進捗遅延の整理")

    def test_登録済みテンプレートを優先する(self):
        PromptTemplate.objects.create(
            tenant=self.tenant, key="own", title="自社標準の棚卸し", body="本文"
        )
        response = self.client.get(reverse("pmo:prompt_library"))

        self.assertContains(response, "自社標準の棚卸し")
        self.assertNotContains(response, "進捗遅延の整理")

    def test_承認POSTで状態が遷移し履歴が残る(self):
        deliverable = self._deliverable(status=Deliverable.Status.PENDING_APPROVAL)
        response = self.client.post(
            reverse("pmo:approvals"), {"deliverable": deliverable.pk, "decision": "approved"}
        )

        deliverable.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(deliverable.status, Deliverable.Status.APPROVED)
        self.assertEqual(
            Approval.objects.filter(deliverable=deliverable, actor=self.user).count(), 1
        )

    def test_根拠不足の成果物はPOSTでも承認できない(self):
        run = self._run()
        EvidenceEvaluation.objects.create(
            run=run, confidence=0.2, recommendation=Recommendation.ASK_CLARIFICATION
        )
        deliverable = self._deliverable(
            agent_run=run, status=Deliverable.Status.PENDING_APPROVAL
        )
        self.client.post(
            reverse("pmo:approvals"), {"deliverable": deliverable.pk, "decision": "approved"}
        )

        deliverable.refresh_from_db()

        self.assertEqual(deliverable.status, Deliverable.Status.PENDING_APPROVAL)
        self.assertFalse(Approval.objects.filter(deliverable=deliverable).exists())

    def test_承認画面が根拠不足の理由を表示する(self):
        run = self._run()
        EvidenceEvaluation.objects.create(
            run=run,
            confidence=0.2,
            recommendation=Recommendation.ASK_CLARIFICATION,
            missing_information=["直近の進捗実績"],
        )
        self._deliverable(agent_run=run, status=Deliverable.Status.PENDING_APPROVAL)
        response = self.client.get(reverse("pmo:approvals"))

        self.assertContains(response, "disabled")
        self.assertContains(response, "直近の進捗実績")

    def test_他テナントの成果物は承認できない(self):
        other = Tenant.objects.create(code="other", name="OTHER")
        other_project = Project.objects.create(tenant=other, code="p9", name="他社案件")
        deliverable = self._deliverable(
            project=other_project, status=Deliverable.Status.PENDING_APPROVAL
        )
        response = self.client.post(
            reverse("pmo:approvals"), {"deliverable": deliverable.pk, "decision": "approved"}
        )

        self.assertEqual(response.status_code, 404)
