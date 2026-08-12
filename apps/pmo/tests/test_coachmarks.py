"""PMO 3 画面のコーチマークと説明ツールチップ（UXP: 初見の読み方）。

この 3 画面は「AI が作った候補」を扱う。初見の利用者が候補を確定情報と読むと、
未確認の記述がそのまま承認へ流れる。そのため次を回帰として固定する。

- 画面の見方（コーチマーク）が、`{% block content %}` の先頭に必ず出る
- AI 生成物を確定情報として扱わせない語（AIの回答 / AI生成本文 / 確定本文 /
  本文の出所）に、意味を開ける説明が付いている

コーチマークは JavaScript で閉じられるが、HTML には常に存在する。
ここでは「描画されていること」を見る。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.pmo.models import Deliverable
from apps.projects.models import Project


class PmoCoachmarkBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-coach",
            email="pmo-coach@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.client.force_login(self.user)


class ConsultationCoachmarkTests(PmoCoachmarkBase):
    def test_相談画面にコーチマークが出る(self):
        response = self.client.get(reverse("pmo:consultation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-coach="pmo-consultation"')
        # 手順は画面に実在する見出しを指す。
        self.assertContains(response, "良い相談文の例")
        self.assertContains(response, "確定した回答ではありません")

    def test_相談画面はAIの回答と根拠評価の説明を持つ(self):
        response = self.client.get(
            reverse("pmo:consultation"),
            {"q": "結合試験が5日遅れています。どう整理すべきですか。"},
        )

        self.assertEqual(response.status_code, 200)
        # 入力欄の直後。結果が出る前から読める位置に置く。
        self.assertContains(response, "AIの回答の説明")
        self.assertContains(response, "確定情報ではありません")
        self.assertContains(response, "根拠評価の説明")
        self.assertContains(response, "意図分類の説明")


class DeliverableCoachmarkTests(PmoCoachmarkBase):
    def _deliverable(self) -> Deliverable:
        return Deliverable.objects.create(
            project=self.project,
            title="週次報告",
            kind=Deliverable.Kind.WEEKLY_REPORT,
            ai_generated_body="今週は結合試験を実施しました。",
            body="今週は結合試験を実施しました。",
        )

    def test_成果物支援にコーチマークとAI生成本文の説明が出る(self):
        response = self.client.get(
            reverse("pmo:deliverables"), {"deliverable": str(self._deliverable().pk)}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-coach="pmo-deliverables"')
        self.assertContains(response, "AI生成本文の説明")
        self.assertContains(response, "確定本文の説明")
        self.assertContains(response, "承認申請の説明")
        # AI 生成本文を確定扱いさせない。
        self.assertContains(response, "人が確認・修正して")


class ApprovalCoachmarkTests(PmoCoachmarkBase):
    def test_承認画面にコーチマークと本文の出所の説明が出る(self):
        response = self.client.get(reverse("pmo:approvals"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-coach="pmo-approvals"')
        self.assertContains(response, "本文の出所の説明")
        self.assertContains(response, "根拠の充足状況の説明")
        # 承認できない理由は区分で読ませる。
        self.assertContains(response, "承認前に確定本文を保存してください")
