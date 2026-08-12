"""AI 介入提案の一覧・判断画面の見え方の検証（UXP-11 / UXP-44）。

見ているのは「判断の前に必要な材料が出ているか」の 1 点。判断ロジック自体は
`test_intervention_decision.py` が担保しているので、ここでは重複させない。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import InterventionProposal
from apps.projects.models import Project


class InterventionScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
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
        self.without_evidence = InterventionProposal.objects.create(
            project=self.project,
            title="要員を1名追加する",
            recommended_action="来週から2名体制へ",
            evidence=[],
            confidence=0.82,
        )
        self.with_evidence = InterventionProposal.objects.create(
            project=self.project,
            title="テスト工程を前倒しする",
            evidence=["残課題が20件", "不具合の再オープン率が上昇"],
            confidence=0.5,
        )
        self.decided = InterventionProposal.objects.create(
            project=self.project,
            title="外注を1社追加する",
            status=InterventionProposal.Status.REJECTED,
            decision_reason="予算超過のため",
            evidence=["見積差異"],
        )
        self.client.force_login(self.user)

    def _list_url(self) -> str:
        return reverse("dashboard:intervention")

    def _decide_url(self, proposal: InterventionProposal) -> str:
        return reverse("dashboard:intervention_decide", args=[proposal.pk])

    def test_一覧に判断フォームを置かない(self):
        """UXP-11: 一覧からは判断できず、判断画面へのリンクだけがある。"""

        response = self.client.get(self._list_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "decision_reason")
        self.assertNotContains(response, "<textarea")
        self.assertNotContains(
            response, f'action="{self._decide_url(self.without_evidence)}"'
        )
        self.assertContains(response, self._decide_url(self.without_evidence))

    def test_一覧で根拠数と信頼度と更新時刻を比較できる(self):
        response = self.client.get(self._list_url())

        for header in ("根拠", "信頼度", "更新時刻", "判断状態"):
            self.assertContains(response, header)

        self.assertContains(response, "82%")
        self.assertContains(response, "2件")

    def test_判断待ちのクイックビューで絞り込める(self):
        """UXP-11: 判断待ちへ 1 クリックで到達でき、選択中だと分かる。"""

        response = self.client.get(self._list_url(), {"status": "proposed"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.without_evidence.title)
        self.assertNotContains(response, self.decided.title)
        self.assertContains(response, "quick-view is-active")

    def test_根拠なしの警告が判断リンクより先に出る(self):
        """UXP-11: 読ませてから進ませる。順序が逆だと警告は素通りされる。"""

        content = self.client.get(self._list_url()).content.decode()
        warning_at = content.index("根拠が1件も記録されていません")
        link_at = content.index(self._decide_url(self.without_evidence))

        self.assertLess(warning_at, link_at)

    def test_0件のとき予兆検知とPMO相談への入口を出す(self):
        InterventionProposal.objects.all().delete()

        response = self.client.get(self._list_url())

        self.assertContains(response, reverse("dashboard:detection"))
        self.assertContains(response, reverse("pmo:consultation"))

    def test_判断画面に選択肢の説明が選択前から出る(self):
        """UXP-44: 何が起きるか・何が必須かを、選ぶ前に読める。"""

        response = self.client.get(self._decide_url(self.with_evidence))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "それぞれの判断で何が起きるか")
        self.assertContains(response, "判断理由 ＋ 修正後のアクション")
        self.assertContains(response, "実施しないことを記録します")

    def test_判断画面の上部に根拠と現在の状態の要約が出る(self):
        response = self.client.get(self._decide_url(self.with_evidence))

        self.assertContains(response, "残課題が20件")
        self.assertContains(response, "50%")
        self.assertContains(response, "AIの候補（未確定）")

    def test_判断画面が1回限りであることを操作の直前に書く(self):
        content = self.client.get(self._decide_url(self.with_evidence)).content.decode()
        notice_at = content.index("この操作は1回限りです")
        button_at = content.index("この内容で判断を記録する")

        self.assertLess(notice_at, button_at)
