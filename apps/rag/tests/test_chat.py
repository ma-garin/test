"""チャットモードの回帰テスト。

`local_hash` Embedding を使うので外部 API を呼ばない。根拠が無いときに断定しない
ことは必須要件なので、必ずテストで固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import Recommendation
from apps.documents.models import Document, DocumentPage, FileType
from apps.rag.models import ChatMessage, ChatSession, RagAnswer, VectorIndex
from apps.rag.services import chat
from apps.rag.services.indexer import rebuild_index


class ChatReplyTests(TestCase):
    """検索結果から応答を組み立てる部分（保存を伴わない）。"""

    def test_根拠がなければ断定せず確認を促す(self):
        reply = chat.build_reply("結合試験の完了判定は？", [])

        self.assertTrue(reply.needs_clarification)
        self.assertEqual(reply.recommendation, Recommendation.ASK_CLARIFICATION)
        self.assertIn("確認させてください", reply.body)
        self.assertEqual(reply.citations, [])

    def test_根拠がなければ断定表現を含めない(self):
        reply = chat.build_reply("進捗が遅れています", [])

        self.assertNotIn("確認できたことは次のとおりです", reply.body)


class ChatViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)
        self.url = reverse("rag:chat")

    def _build_index(self) -> VectorIndex:
        index = VectorIndex.objects.create(tenant=self.tenant)
        self._document("テスト管理標準", "結合試験の進捗管理では、消化率と不具合収束状況から完了判定を行う。")
        self._document("品質管理標準", "完了判定は不具合の収束傾向と未消化ケースの残量で判断する。")
        rebuild_index(index)

        return index

    def _document(self, title: str, body: str) -> Document:
        document = Document.objects.create(
            tenant=self.tenant,
            title=title,
            file="dummy.pdf",
            file_type=FileType.PDF,
        )
        DocumentPage.objects.create(document=document, page_number=1, content=body)

        return document

    def test_画面が200を返す(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "会話履歴")

    def test_送信すると会話が保存されリダイレクトする(self):
        self._build_index()
        response = self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})

        self.assertEqual(response.status_code, 302)
        session = ChatSession.objects.get(user=self.user)
        self.assertEqual(session.messages.count(), 2)
        self.assertEqual(session.messages.first().role, ChatMessage.Role.USER)

    def test_引用付きの応答を返す(self):
        self._build_index()
        self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})

        answer = RagAnswer.objects.get()
        self.assertTrue(answer.citations.exists())
        self.assertIn("テスト管理標準", answer.grounded_findings)

    def test_インデックス未構築なら確認を促す応答になる(self):
        self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})

        assistant = ChatMessage.objects.get(role=ChatMessage.Role.ASSISTANT)
        self.assertIsNone(assistant.answer)
        self.assertIn("確認させてください", assistant.content)

    def test_会話履歴が画面に表示される(self):
        self._build_index()
        self.client.post(self.url, {"message": "結合試験の完了判定はどう決める？"})
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "結合試験の完了判定はどう決める？")
        self.assertContains(response, "テスト管理標準")

    def test_空入力は保存しない(self):
        response = self.client.post(self.url, {"message": "   "})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_他人のセッションは開けない(self):
        other_user = User.objects.create_user(
            username="other",
            email="other@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.VIEWER,
        )
        session = ChatSession.objects.create(
            tenant=self.tenant, user=other_user, title="他人の会話"
        )
        response = self.client.get(f"{self.url}?session={session.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "他人の会話")
