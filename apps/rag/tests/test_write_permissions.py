"""チャット・RAG 評価の書き込みに対する権限テスト。

チャットは会話の記録を、評価は実行結果と Golden を作る。どちらも案件に
紐づかないテナント単位の書き込みなので、テナントロールで判定する。
参照専用ロールから直接 POST して 403 になること、かつレコードが 1 件も
増えないことを対で確かめる。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.rag.models import ChatMessage, ChatSession, EvaluationRun, GoldenQuestion


class RagWritePermissionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.viewer = self._user("viewer", Role.VIEWER)
        # 案件に紐づかない操作なので、テナントロールで編集できる立場と比べる。
        self.editor = self._user("editor", Role.CHANGE_MANAGER)

    def _user(self, name: str, role: str) -> User:
        return User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=role,
        )

    # --- チャット -----------------------------------------------------------

    def test_参照専用ロールは会話を記録できない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("rag:chat"), {"message": "いま止まっているタスクは何ですか"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ChatSession.objects.count(), 0)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_参照専用ロールでもチャット画面は開ける(self):
        self.client.force_login(self.viewer)

        self.assertEqual(self.client.get(reverse("rag:chat")).status_code, 200)

    def test_編集できる立場なら会話を記録できる(self):
        """締めすぎていないこと。質問の送信は編集権限で行える。"""

        self.client.force_login(self.editor)

        response = self.client.post(
            reverse("rag:chat"), {"message": "いま止まっているタスクは何ですか"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ChatSession.objects.count(), 1)

    # --- RAG 評価 -----------------------------------------------------------

    def test_参照専用ロールは評価を実行できない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(reverse("rag:evaluation"), {"suite": "retrieval"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(EvaluationRun.objects.count(), 0)

    def test_参照専用ロールはGoldenを登録できない(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("rag:evaluation"),
            {"action": "add_golden", "question": "権限がないのに登録した設問"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(GoldenQuestion.objects.count(), 0)

    def test_参照専用ロールでも評価画面は開ける(self):
        self.client.force_login(self.viewer)

        self.assertEqual(self.client.get(reverse("rag:evaluation")).status_code, 200)

    def test_編集できる立場ならGoldenを登録できる(self):
        self.client.force_login(self.editor)

        response = self.client.post(
            reverse("rag:evaluation"),
            {"action": "add_golden", "question": "結合試験の完了判定はどう決める？"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(GoldenQuestion.objects.count(), 1)
