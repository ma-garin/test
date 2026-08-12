"""Agentic トレース画面の検証（UXP-26 / UXP-27）。

監査で最初に必要なのは「失敗した実行」と「根拠が足りない実行」への到達で、
一覧が全件を並べるだけだとその 2 つが埋もれる。ここでは表示の見栄えではなく
「危険な実行だけが残ること」「詳細で結論が先に読めること」を確認する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import (
    AgentRun,
    AgentStep,
    EvidenceEvaluation,
    HumanReview,
    Level,
    Recommendation,
)


class AgentRunListTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="trace-viewer",
            email="trace-viewer@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        self.healthy = AgentRun.objects.create(
            tenant=self.tenant,
            area=AgentRun.Area.RAG_SEARCH,
            status=AgentRun.Status.SUCCEEDED,
            user_input="仕様書の記載場所を知りたい",
        )
        EvidenceEvaluation.objects.create(
            run=self.healthy,
            confidence=0.92,
            relevance=Level.HIGH,
            coverage=Level.HIGH,
            recommendation=Recommendation.ANSWER,
        )

        self.failed = AgentRun.objects.create(
            tenant=self.tenant,
            area=AgentRun.Area.PMO_CONSULTATION,
            status=AgentRun.Status.FAILED,
            user_input="遅延の原因を整理したい",
            error_message="外部APIがタイムアウトしました",
        )

        self.weak = AgentRun.objects.create(
            tenant=self.tenant,
            area=AgentRun.Area.RAG_CHAT,
            status=AgentRun.Status.SUCCEEDED,
            user_input="この見積は妥当か",
        )
        EvidenceEvaluation.objects.create(
            run=self.weak,
            confidence=0.21,
            recommendation=Recommendation.ASK_CLARIFICATION,
        )

    def _ids(self, response) -> set:
        return {run.pk for run in response.context["runs"]}

    def test_クイックビューで失敗と根拠不足だけを抽出できる(self) -> None:
        """UXP-26: 監査の起点。1 クリックで危険な実行だけが残ること。"""

        response = self.client.get(reverse("agents:run_list"), {"attention": "1"})

        self.assertEqual(self._ids(response), {self.failed.pk, self.weak.pk})
        self.assertTrue(response.context["filters"].is_active)

    def test_領域と成否と根拠評価で絞り込める(self) -> None:
        """UXP-26: 3 軸が独立に効くこと。"""

        by_area = self.client.get(reverse("agents:run_list"), {"area": AgentRun.Area.RAG_CHAT})
        by_status = self.client.get(reverse("agents:run_list"), {"status": AgentRun.Status.FAILED})
        by_evidence = self.client.get(reverse("agents:run_list"), {"evidence": "blocked"})

        self.assertEqual(self._ids(by_area), {self.weak.pk})
        self.assertEqual(self._ids(by_status), {self.failed.pk})
        self.assertEqual(self._ids(by_evidence), {self.weak.pk})

    def test_不正な絞り込み値では全件へ倒す(self) -> None:
        """URL は手で編集される。500 を返さず全件を見せる。"""

        response = self.client.get(
            reverse("agents:run_list"), {"status": "zzz", "evidence": "'; drop--"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._ids(response)), 3)
        self.assertFalse(response.context["filters"].is_active)

    def test_一覧の行から詳細へ進める(self) -> None:
        """UXP-26: 抽出した実行をその場で開けること。"""

        response = self.client.get(reverse("agents:run_list"))

        self.assertContains(response, reverse("agents:run_detail", args=[self.failed.pk]))
        self.assertContains(response, "トレースを見る")


class AgentRunDetailTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="globex", name="Globex")
        self.user = User.objects.create_user(
            username="trace-auditor",
            email="trace-auditor@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        self.run = AgentRun.objects.create(
            tenant=self.tenant,
            area=AgentRun.Area.PMO_CONSULTATION,
            status=AgentRun.Status.SUCCEEDED,
            user_input="残課題の優先度を決めたい",
        )
        EvidenceEvaluation.objects.create(
            run=self.run,
            confidence=0.3,
            recommendation=Recommendation.ASK_CLARIFICATION,
            has_conflict=True,
        )
        AgentStep.objects.create(
            run=self.run,
            order=1,
            tool_name="search_documents",
            input_summary="残課題 優先度",
            output_summary="3件ヒット",
        )
        HumanReview.objects.create(
            run=self.run,
            reviewer=self.user,
            decision=HumanReview.Decision.MODIFIED,
            comment="優先度の根拠を差し替えた",
        )

    def test_要約カードに成否と根拠と人の確認が出る(self) -> None:
        """UXP-27: 監査で最初に必要な結論を、経過より先に読ませる。"""

        response = self.client.get(reverse("agents:run_detail", args=[self.run.pk]))
        body = response.content.decode()

        self.assertContains(response, "この実行の要約")
        self.assertContains(response, "根拠不足")
        self.assertContains(response, "修正して採用")
        self.assertLess(body.index("この実行の要約"), body.index("処理ステップ"))

    def test_処理ステップと入力を折りたたんでいる(self) -> None:
        """UXP-27: 経過は既定で畳み、結論を押し流さない。"""

        response = self.client.get(reverse("agents:run_detail", args=[self.run.pk]))
        body = response.content.decode()

        self.assertGreaterEqual(body.count("<details"), 2)
        self.assertContains(response, "処理ステップを開く")
        self.assertContains(response, "入力全文を開く")

    def test_他テナントの実行は開けない(self) -> None:
        """絞り込みを足してもテナント分離が抜けないこと。"""

        foreign = AgentRun.objects.create(
            tenant=self.other_tenant,
            area=AgentRun.Area.RAG_SEARCH,
            status=AgentRun.Status.SUCCEEDED,
            user_input="他テナントの実行",
        )

        response = self.client.get(reverse("agents:run_detail", args=[foreign.pk]))

        self.assertEqual(response.status_code, 404)
