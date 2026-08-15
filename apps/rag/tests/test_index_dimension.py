"""Embedding の次元が食い違うインデックスを検索したときの挙動。

openai（1536次元）で構築したインデックスのまま API キーを外すと、問い合わせ側は
local_hash（256次元）へ退避する。ここで例外にすると検索もチャットも 500 になり、
利用者には「壊れた」以上のことが分からない。落とさずに動かし、**再構築が必要で
あることが分かる**ことを確かめる。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.documents.models import Document, DocumentPage, FileType
from apps.rag.models import VectorIndex
from apps.rag.services.embeddings import cosine_similarity
from apps.rag.services.indexer import rebuild_index
from apps.rag.services.retriever import search
from apps.rag.services.vector_store import get_vector_store

#: openai の text-embedding-3-small と同じ次元。
OPENAI_DIMENSION = 1536


class CosineSimilarityTests(TestCase):
    def test_次元が違っても例外にしない(self):
        self.assertEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0, 0.0]), 0.0)

    def test_同じ次元なら内積を返す(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)


class StaleIndexSearchTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.index = VectorIndex.objects.create(tenant=self.tenant)
        document = Document.objects.create(
            tenant=self.tenant, title="テスト管理標準", file="dummy.pdf", file_type=FileType.PDF
        )
        DocumentPage.objects.create(
            document=document,
            page_number=1,
            content="結合試験の進捗管理では、消化率と不具合収束状況から完了判定を行う。",
        )
        rebuild_index(self.index)
        self.index.refresh_from_db()

    def _make_dimension_mismatch(self) -> None:
        """openai で構築したインデックスの状態を作る。

        ベクトル実体を 1536 次元へ置き換え、記録上の次元も合わせる。
        問い合わせ側は API キーが無いため local_hash（256次元）になる。
        """

        store = get_vector_store(self.index)
        store.upsert(
            {
                chunk_id: [0.0] * OPENAI_DIMENSION
                for chunk_id, _ in list(store.iter_vectors())
            }
        )
        VectorIndex.objects.filter(pk=self.index.pk).update(dimension=OPENAI_DIMENSION)
        self.index.refresh_from_db()

    def test_次元が違っても検索が落ちない(self):
        self._make_dimension_mismatch()

        hits = search(self.index, "結合試験の完了判定")

        # 語彙検索だけは効くので、検索画面は 0 件にも 500 にもならない。
        self.assertTrue(hits)
        self.assertTrue(all(hit.vector_score is None for hit in hits))

    def test_再構築が必要であることが分かる(self):
        self._make_dimension_mismatch()

        self.assertTrue(self.index.is_stale)
        self.assertIn("次元", self.index.rebuild_required_reason)
        self.assertIn(str(OPENAI_DIMENSION), self.index.rebuild_required_reason)

    def test_構築時のプロバイダが変わっていても分かる(self):
        VectorIndex.objects.filter(pk=self.index.pk).update(embedding_provider="openai")
        self.index.refresh_from_db()

        self.assertTrue(self.index.is_stale)
        self.assertIn("openai", self.index.rebuild_required_reason)

    def test_設定どおりのインデックスは再構築不要(self):
        self.assertEqual(self.index.rebuild_required_reason, "")
        self.assertFalse(self.index.is_stale)

    def test_相談画面が再構築の必要を利用者へ伝える(self):
        from django.urls import reverse

        from apps.accounts.constants import Role
        from apps.accounts.models import User

        self._make_dimension_mismatch()
        user = User.objects.create_user(
            username="pmo-user",
            email="pmo-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("pmo:consultation"), {"q": "進捗の遅延を整理したい"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "再構築")
