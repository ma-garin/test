"""フィードバック投稿画面の書き方ガイド（UXP-45）。

「役に立たなかった」だけの投稿は改善に使えない。何を書けば再現できるかを
先に示し、事実誤認を選ぶ前に必須情報が読めることを固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User


class FeedbackFormScreenTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme-fb", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-feedback",
            email="pmo-feedback@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.client.force_login(self.user)

        self.url = reverse("audit:feedback_create")

    def test_4項目の入力例が具体例つきで出る(self):
        response = self.client.get(self.url)

        for label in ("再現手順", "期待結果", "実際の結果", "影響範囲"):
            self.assertContains(response, label)

        # 概念語だけでなく、そのまま貼れる文言を出す。
        self.assertContains(response, "ISS-102")
        self.assertContains(response, "PMO の週次報告")

    def test_事実誤認で必須になる情報を選択前に示す(self):
        response = self.client.get(self.url)
        html = response.content.decode()

        self.assertContains(response, "「事実誤認があった」を選ぶ前に")
        self.assertLess(
            html.index("「事実誤認があった」を選ぶ前に"),
            html.index('name="has_fact_error"'),
        )

    def test_送信後に一覧へ戻る意味を示す(self):
        response = self.client.get(self.url)

        self.assertContains(response, "フィードバック集計（一覧）へ戻ります")
        self.assertContains(response, "事実誤認 0 件")
