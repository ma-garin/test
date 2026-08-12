"""フィードバック投稿の検証。

集計画面の数字がそのまま受入条件の判定に使われるため、
「他テナントの対象に紐づけられないこと」を最優先で確認する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun
from apps.audit.forms import FeedbackForm
from apps.audit.models import Feedback, OperationLog


class FeedbackSubmitTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="Globex")
        self.user = User.objects.create_user(
            username="reviewer",
            email="reviewer@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.run = AgentRun.objects.create(
            tenant=self.tenant, area=AgentRun.Area.RAG_SEARCH, user_input="進捗を教えて"
        )
        self.other_run = AgentRun.objects.create(
            tenant=self.other_tenant, area=AgentRun.Area.RAG_SEARCH, user_input="他社の質問"
        )
        self.client.force_login(self.user)
        self.url = reverse("audit:feedback_create")

    def test_投稿すると一覧へ戻り集計に反映される(self):
        response = self.client.post(
            self.url,
            {
                "rating": Feedback.Rating.GOOD,
                "agent_run": self.run.pk,
                "answer": "",
                "comment": "根拠の出典が明確だった",
            },
        )

        self.assertRedirects(response, reverse("audit:feedback_list"))

        feedback = Feedback.objects.get()
        self.assertEqual(feedback.tenant, self.tenant)
        self.assertEqual(feedback.user, self.user)
        self.assertEqual(feedback.agent_run, self.run)
        self.assertFalse(feedback.has_fact_error)
        self.assertTrue(OperationLog.objects.filter(action="feedback.submit").exists())

    def test_事実誤認ありも記録できる(self):
        self.client.post(
            self.url,
            {
                "rating": Feedback.Rating.BAD,
                "has_fact_error": "on",
                "comment": "存在しない章を引用していた",
            },
        )

        feedback = Feedback.objects.get()
        self.assertTrue(feedback.has_fact_error)
        self.assertEqual(feedback.rating, Feedback.Rating.BAD)

    def test_否定評価はコメントが必須(self):
        response = self.client.post(self.url, {"rating": Feedback.Rating.BAD, "comment": " "})

        self.assertEqual(response.status_code, 200)
        self.assertIn("comment", response.context["form"].errors)
        self.assertFalse(Feedback.objects.exists())

    def test_他テナントの対象には紐づけられない(self):
        response = self.client.post(
            self.url,
            {"rating": Feedback.Rating.GOOD, "agent_run": self.other_run.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("agent_run", response.context["form"].errors)
        self.assertFalse(Feedback.objects.exists())

    def test_選択肢は自テナントの実行だけ(self):
        form = FeedbackForm(tenant=self.tenant)

        self.assertEqual(
            list(form.fields["agent_run"].queryset.values_list("pk", flat=True)), [self.run.pk]
        )

    def test_秘密値はマスクされて保存される(self):
        self.client.post(
            self.url,
            {"rating": Feedback.Rating.GOOD, "comment": "api_key: sk-abcdefgh12345678 が出た"},
        )

        self.assertNotIn("sk-abcdefgh12345678", Feedback.objects.get().comment)

    def test_未ログインは投稿できない(self):
        self.client.logout()
        response = self.client.post(self.url, {"rating": Feedback.Rating.GOOD})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Feedback.objects.exists())

    def test_一覧に投稿導線がある(self):
        response = self.client.get(reverse("audit:feedback_list"))

        self.assertContains(response, self.url)
