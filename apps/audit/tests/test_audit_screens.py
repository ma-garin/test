"""操作ログ・フィードバック画面の検証（UXP-28 / UXP-29）。

操作ログは「失敗した操作を、いつ・誰が・何に対して行ったか」へ辿り着けること、
フィードバックは「直すべき指摘から順に見られること」が用途。表示を足しても
保存時のマスクを迂回しないことを併せて確認する。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun, HumanReview
from apps.audit.models import Feedback, OperationLog


class OperationLogScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="ops-auditor",
            email="ops-auditor@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.other = User.objects.create_user(
            username="ops-member",
            email="ops-member@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.VIEWER,
        )
        self.client.force_login(self.user)

        self.ok_log = OperationLog.objects.create(
            tenant=self.tenant,
            user=self.user,
            action="タスク更新",
            target="タスク: 要件定義",
            succeeded=True,
        )
        self.ng_log = OperationLog.objects.create(
            tenant=self.tenant,
            user=self.other,
            action="連携同期",
            target="接続: Redmine",
            succeeded=False,
            detail="接続がタイムアウトしました",
        )
        self.old_log = OperationLog.objects.create(
            tenant=self.tenant,
            user=self.user,
            action="タスク更新",
            target="タスク: 旧計画",
            succeeded=True,
        )
        OperationLog.objects.filter(pk=self.old_log.pk).update(
            created_at=timezone.now() - timedelta(days=60)
        )

    def _ids(self, response) -> set:
        return {log.pk for log in response.context["logs"]}

    def test_期間と成否で絞り込める(self) -> None:
        """UXP-28: 既定は全期間。期間指定で古い記録を落とせること。"""

        default = self.client.get(reverse("audit:operation_list"))
        recent = self.client.get(reverse("audit:operation_list"), {"period": 30})
        failed = self.client.get(reverse("audit:operation_list"), {"result": "ng"})

        self.assertEqual(len(self._ids(default)), 3)
        self.assertEqual(self._ids(recent), {self.ok_log.pk, self.ng_log.pk})
        self.assertEqual(self._ids(failed), {self.ng_log.pk})

    def test_操作者と対象種別で絞り込める(self) -> None:
        """UXP-28: 誰が・何に対して、の 2 軸。"""

        by_user = self.client.get(reverse("audit:operation_list"), {"user": self.other.pk})
        by_action = self.client.get(reverse("audit:operation_list"), {"action": "タスク更新"})
        by_target = self.client.get(reverse("audit:operation_list"), {"target": "Redmine"})

        self.assertEqual(self._ids(by_user), {self.ng_log.pk})
        self.assertEqual(self._ids(by_action), {self.ok_log.pk, self.old_log.pk})
        self.assertEqual(self._ids(by_target), {self.ng_log.pk})

    def test_失敗理由と件数と適用条件を画面に出す(self) -> None:
        """UXP-28: 何件が、どの条件で残っているのかを画面内で示す。"""

        response = self.client.get(reverse("audit:operation_list"), {"result": "ng"})

        self.assertContains(response, "失敗理由")
        self.assertContains(response, "接続がタイムアウトしました")
        self.assertContains(response, "適用条件")
        self.assertContains(response, "失敗のみ")
        self.assertContains(response, "条件をクリア")
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_失敗理由に秘密値を出さない(self) -> None:
        """列を足してもマスク済みの本文しか出さないこと。"""

        OperationLog.objects.create(
            tenant=self.tenant,
            user=self.user,
            action="APIキー検証",
            target="接続: OpenAI",
            succeeded=False,
            detail="api_key=sk-abcdefgh12345678 で認証に失敗しました",
        )

        response = self.client.get(reverse("audit:operation_list"), {"result": "ng"})

        self.assertNotContains(response, "sk-abcdefgh12345678")
        self.assertContains(response, "[REDACTED]")


class FeedbackScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="fb-auditor",
            email="fb-auditor@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        self.run = AgentRun.objects.create(
            tenant=self.tenant,
            area=AgentRun.Area.RAG_CHAT,
            status=AgentRun.Status.SUCCEEDED,
            user_input="納期の根拠を教えて",
        )
        HumanReview.objects.create(
            run=self.run,
            reviewer=self.user,
            decision=HumanReview.Decision.ACCEPTED,
            comment="回答を採用",
        )

        self.fact_error = Feedback.objects.create(
            tenant=self.tenant,
            user=self.user,
            rating=Feedback.Rating.BAD,
            comment="納期が実際と違う。案件Aの2月分で再現する。",
            has_fact_error=True,
            agent_run=self.run,
        )
        self.plain = Feedback.objects.create(
            tenant=self.tenant,
            user=self.user,
            rating=Feedback.Rating.GOOD,
            comment="",
        )
        self.neutral = Feedback.objects.create(
            tenant=self.tenant,
            user=self.user,
            rating=Feedback.Rating.NEUTRAL,
            comment="もう少し具体的だと助かる",
        )

    def _ids(self, response) -> set:
        return {feedback.pk for feedback in response.context["feedbacks"]}

    def test_事実誤認のクイックフィルタで明細を絞れる(self) -> None:
        """UXP-29: 受入条件に直結する指摘へ 1 クリックで到達する。"""

        response = self.client.get(reverse("audit:feedback_list"), {"fact": "error"})

        self.assertEqual(self._ids(response), {self.fact_error.pk})
        self.assertContains(response, "事実誤認あり")

    def test_明細に再現情報と対応状況を出す(self) -> None:
        """UXP-29: 直せる指摘かどうかを一覧で判別できること。"""

        response = self.client.get(reverse("audit:feedback_list"))
        repro = {feedback.pk: feedback.has_repro for feedback in response.context["feedbacks"]}

        self.assertTrue(repro[self.fact_error.pk])
        self.assertFalse(repro[self.plain.pk])
        self.assertFalse(repro[self.neutral.pk])
        self.assertContains(response, "再現情報")
        self.assertContains(response, "対応済み")
        self.assertContains(response, "未対応")

    def test_既定の並びは直すべき順になる(self) -> None:
        """UXP-29: 事実誤認 → 低評価 → 新着。新着順だと指摘が沈む。"""

        response = self.client.get(reverse("audit:feedback_list"))
        order = [feedback.pk for feedback in response.context["feedbacks"]]

        self.assertEqual(order[0], self.fact_error.pk)
        self.assertEqual(response.context["view_filters"].sort, "priority")

    def test_クイックフィルタでも集計は条件全体から出す(self) -> None:
        """絞り込むたびに事実誤認率が 100% になると、受入条件の判定に使えない。"""

        response = self.client.get(reverse("audit:feedback_list"), {"fact": "error"})

        self.assertEqual(response.context["stats"].total, 3)
        self.assertEqual(response.context["stats"].fact_error_count, 1)
        self.assertEqual(response.context["page"].paginator.count, 1)
