"""開いている画面情報の自動読込テスト。

画面文脈は「渡ったか」だけでなく「回答に明示されたか」まで確かめる。
引き渡せていても回答へ現れなければ、利用者から見て機能していないため。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun
from apps.agents.services import orchestrator, screen_context
from apps.projects.models import Project


class ScreenContextResolveTests(TestCase):
    def test_画面キーが空なら文脈なし(self):
        self.assertIsNone(screen_context.resolve("", "案件1"))
        self.assertIsNone(screen_context.resolve(None))

    def test_リスク画面の確認観点に対策の有無と期限が含まれる(self):
        context = screen_context.resolve("risk_list", "案件1")

        self.assertIn("対策の有無", context.viewpoints)
        self.assertIn("対応期限", context.viewpoints)

    def test_見出しは画面名と対象を含む(self):
        context = screen_context.resolve("risk_list", "案件1")

        self.assertEqual(context.headline, "リスク一覧画面の案件1について")

    def test_対象がなくても見出しを作れる(self):
        self.assertEqual(screen_context.resolve("risk_list").headline, "リスク一覧画面について")

    def test_未知の画面キーでも相談を止めない(self):
        context = screen_context.resolve("unknown_screen", "何か")

        self.assertIsNotNone(context)
        self.assertEqual(context.label, "現在の画面")

    def test_対象名は長すぎれば切り詰める(self):
        context = screen_context.resolve("risk_list", "あ" * 300)

        self.assertEqual(len(context.subject), 120)

    def test_相談本文へ画面文脈を付ける(self):
        context = screen_context.resolve("task_detail", "W-10 結合試験")
        decorated = context.decorate("遅れています")

        self.assertIn("WBSタスク詳細画面のW-10 結合試験について", decorated)
        self.assertIn("遅れています", decorated)


class OrchestratorScreenContextTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def test_画面文脈は入力として保存される(self):
        context = screen_context.resolve("risk_list", "案件1")
        result = orchestrator.run(
            tenant=self.tenant,
            question="対策が決まっていないリスクをどう扱いますか",
            area=AgentRun.Area.PMO_CONSULTATION,
            screen_context=context,
        )

        self.assertIn("リスク一覧画面の案件1について", result.run.user_input)
        self.assertEqual(result.run.plan["screen_context"]["key"], "risk_list")

    def test_回答の期待出力に画面と対象が明示される(self):
        context = screen_context.resolve("risk_list", "案件1")
        result = orchestrator.run(
            tenant=self.tenant,
            question="対策が決まっていないリスクをどう扱いますか",
            area=AgentRun.Area.PMO_CONSULTATION,
            screen_context=context,
        )

        self.assertTrue(result.plan.expected_output.startswith("リスク一覧画面の案件1について、"))
        self.assertIn("対策の有無", result.plan.expected_output)

    def test_画面文脈は意図分類を歪めない(self):
        # 画面名に「リスク」が含まれても、相談内容が遅延ならDELAYのまま。
        context = screen_context.resolve("risk_list", "案件1")
        with_context = orchestrator.run(
            tenant=self.tenant,
            question="結合試験が5日遅れています",
            area=AgentRun.Area.PMO_CONSULTATION,
            screen_context=context,
        )
        without_context = orchestrator.run(
            tenant=self.tenant,
            question="結合試験が5日遅れています",
            area=AgentRun.Area.PMO_CONSULTATION,
        )

        self.assertEqual(with_context.run.intent, without_context.run.intent)

    def test_画面文脈がなければ従来どおり(self):
        result = orchestrator.run(
            tenant=self.tenant,
            question="結合試験が遅れています",
            area=AgentRun.Area.PMO_CONSULTATION,
        )

        self.assertEqual(result.run.user_input, "結合試験が遅れています")
        self.assertIsNone(result.run.plan["screen_context"])
        self.assertEqual(result.run.steps.count(), 4)


class ConsultationScreenContextViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.user = User.objects.create_user(
            username="pmo@example.com",
            email="pmo@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_画面文脈が相談へ引き渡される(self):
        response = self.client.get(
            reverse("pmo:consultation"),
            {"q": "対策が決まっていないリスクがあります", "screen": "risk_list", "subject": "案件1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["screen_context"].key, "risk_list")
        self.assertContains(response, "リスク一覧画面")
        self.assertContains(response, 'name="screen"')

    def test_相談前でも画面の確認観点を提示する(self):
        response = self.client.get(
            reverse("pmo:consultation"), {"screen": "risk_list", "subject": "案件1"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])
        self.assertContains(response, "対策の有無")

    def test_画面文脈なしでも従来どおり相談できる(self):
        response = self.client.get(reverse("pmo:consultation"), {"q": "進捗が遅れています"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["screen_context"])

    def test_案件詳細から相談への導線がある(self):
        response = self.client.get(reverse("projects:detail", args=[self.project.pk]))

        self.assertContains(response, "screen=project_detail")
