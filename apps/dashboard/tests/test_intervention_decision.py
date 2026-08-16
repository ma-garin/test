"""AI 介入提案に対する人の判断の検証。

重点は 3 つ。判断理由なしでは記録できないこと、他テナントの提案を
判断できないこと、判断済みを上書きできないこと。いずれも欠けると
「AI の提案には必ず人の判断が残る」という前提が崩れる。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.audit.models import OperationLog
from apps.dashboard.models import InterventionProposal
from apps.dashboard.services.interventions import (
    AlreadyDecidedError,
    decide_intervention,
)
from apps.projects.models import Project


class InterventionDecisionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="Globex")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(
            tenant=self.tenant, code="p-001", name="社内DX"
        )
        self.other_project = Project.objects.create(
            tenant=self.other_tenant, code="p-999", name="他社案件"
        )
        self.proposal = InterventionProposal.objects.create(
            project=self.project,
            title="要員を1名追加する",
            recommended_action="来週から2名体制へ",
        )
        self.other_proposal = InterventionProposal.objects.create(
            project=self.other_project, title="他テナントの提案"
        )
        self.client.force_login(self.user)

    def _url(self, proposal: InterventionProposal) -> str:
        return reverse("dashboard:intervention_decide", args=[proposal.pk])

    def test_採用を判断者と理由つきで記録する(self):
        response = self.client.post(
            self._url(self.proposal),
            {"status": "accepted", "decision_reason": "工数見積の根拠に納得したため", "modified_action": ""},
        )

        self.assertRedirects(response, reverse("dashboard:intervention"))

        saved = InterventionProposal.objects.get(pk=self.proposal.pk)
        self.assertEqual(saved.status, InterventionProposal.Status.ACCEPTED)
        self.assertEqual(saved.decided_by, self.user)
        self.assertIsNotNone(saved.decided_at)
        self.assertEqual(saved.decision_reason, "工数見積の根拠に納得したため")
        self.assertTrue(
            OperationLog.objects.filter(action="intervention.decide", project=self.project).exists()
        )

    def test_判断理由が空なら保存できない(self):
        response = self.client.post(
            self._url(self.proposal), {"status": "accepted", "decision_reason": "   "}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("decision_reason", response.context["form"].errors)

        saved = InterventionProposal.objects.get(pk=self.proposal.pk)
        self.assertEqual(saved.status, InterventionProposal.Status.PROPOSED)
        self.assertIsNone(saved.decided_at)

    def test_修正して採用は修正後アクションが必須(self):
        response = self.client.post(
            self._url(self.proposal),
            {"status": "modified", "decision_reason": "範囲を狭めて実施", "modified_action": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("modified_action", response.context["form"].errors)
        self.assertEqual(
            InterventionProposal.objects.get(pk=self.proposal.pk).status,
            InterventionProposal.Status.PROPOSED,
        )

    def test_修正して採用は修正後の本文を保存する(self):
        self.client.post(
            self._url(self.proposal),
            {
                "status": "modified",
                "decision_reason": "範囲を狭めて実施",
                "modified_action": "1名ではなく0.5名で対応する",
            },
        )

        saved = InterventionProposal.objects.get(pk=self.proposal.pk)
        self.assertEqual(saved.status, InterventionProposal.Status.MODIFIED)
        self.assertEqual(saved.modified_action, "1名ではなく0.5名で対応する")

    def test_他テナントの提案は判断できない(self):
        response = self.client.post(
            self._url(self.other_proposal),
            {"status": "accepted", "decision_reason": "越境の試み"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            InterventionProposal.objects.get(pk=self.other_proposal.pk).status,
            InterventionProposal.Status.PROPOSED,
        )

    def test_他テナントの提案は編集画面も開けない(self):
        self.assertEqual(self.client.get(self._url(self.other_proposal)).status_code, 404)

    def test_判断済みは再判断できない(self):
        decide_intervention(
            self.proposal, user=self.user, status="rejected", decision_reason="別案を採用"
        )

        response = self.client.post(
            self._url(self.proposal),
            {"status": "accepted", "decision_reason": "やっぱり採用"},
        )

        self.assertRedirects(response, reverse("dashboard:intervention"))

        saved = InterventionProposal.objects.get(pk=self.proposal.pk)
        self.assertEqual(saved.status, InterventionProposal.Status.REJECTED)
        self.assertEqual(saved.decision_reason, "別案を採用")

    def test_サービス層も判断済みを弾く(self):
        decided = decide_intervention(
            self.proposal, user=self.user, status="accepted", decision_reason="理由あり"
        )

        with self.assertRaises(AlreadyDecidedError):
            decide_intervention(
                decided, user=self.user, status="rejected", decision_reason="上書きの試み"
            )

    def test_未ログインは判断画面へ入れない(self):
        self.client.logout()
        response = self.client.get(self._url(self.proposal))

        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response["Location"])

    def test_一覧に判断フォームが出る(self):
        response = self.client.get(reverse("dashboard:intervention"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self._url(self.proposal))
        self.assertNotContains(response, self._url(self.other_proposal))
