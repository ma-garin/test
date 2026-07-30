"""検索経路の回帰テスト。

`local_hash` Embedding を使うため、外部 API なしで検索の端から端まで通る。
"""

from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.documents.models import Document, DocumentPage, DocumentStatus, FileType
from apps.rag.models import VectorIndex
from apps.rag.services.embeddings import LocalHashEmbedder, cosine_similarity
from apps.rag.services.indexer import rebuild_index, split_text
from apps.rag.services.lexical import LexicalIndex
from apps.rag.services.retriever import search
from apps.rag.services.tokenizer import tokenize


class TokenizerTests(TestCase):
    def test_英数字はそのままトークンになる(self):
        self.assertIn("wbs", tokenize("WBS management"))

    def test_日本語は語と文字bigramの両方を返す(self):
        tokens = tokenize("進捗管理")

        self.assertIn("進捗管理", tokens)
        self.assertIn("進捗", tokens)
        self.assertIn("捗管", tokens)

    def test_2文字の日本語はbigram展開しない(self):
        # 3 文字未満は元の語だけ。旧実装と同じ挙動を保つ。
        self.assertEqual(tokenize("品質"), ["品質"])


class SplitTextTests(TestCase):
    def test_重なりを持って分割する(self):
        # chunk_size 100 / overlap 20 なので送り幅は 80。250 文字は 3 チャンクになる。
        chunks = split_text("あ" * 250, chunk_size=100, overlap=20)

        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 100)
        self.assertEqual(len(chunks[-1]), 90)

    def test_空文字は分割しない(self):
        self.assertEqual(split_text("   "), [])

    def test_overlapがchunk_size以上なら例外(self):
        with self.assertRaises(ValueError):
            split_text("text", chunk_size=10, overlap=10)


class LexicalIndexTests(TestCase):
    def test_一致語の多いチャンクが上位になる(self):
        index = LexicalIndex.build(
            [
                ("a", "結合試験の進捗管理と遅延対応について"),
                ("b", "経費精算の申請手順について"),
            ]
        )
        hits = index.search("結合試験 進捗管理")

        self.assertEqual(hits[0].chunk_id, "a")

    def test_一致語がなければ結果は空(self):
        index = LexicalIndex.build([("a", "経費精算の申請手順")])

        self.assertEqual(index.search("zzzzz"), [])


class LocalHashEmbedderTests(TestCase):
    def test_同じ文は同じベクトルになる(self):
        embedder = LocalHashEmbedder(dimension=64)

        self.assertEqual(embedder.embed_one("品質管理"), embedder.embed_one("品質管理"))

    def test_同じ語を含む文の方が近い(self):
        embedder = LocalHashEmbedder(dimension=256)
        query = embedder.embed_one("結合試験の遅延")
        near = embedder.embed_one("結合試験の遅延が発生している")
        far = embedder.embed_one("経費精算の申請手順")

        self.assertGreater(cosine_similarity(query, near), cosine_similarity(query, far))


class SearchTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.index = VectorIndex.objects.create(tenant=self.tenant)

        self.relevant = self._document(
            "テスト管理標準",
            "結合試験の進捗管理では、消化率と不具合収束状況から完了判定を行う。",
        )
        self.other = self._document(
            "経費精算マニュアル",
            "経費精算は毎月末日までに申請し、上長承認を得ること。",
        )

        rebuild_index(self.index)

    def _document(self, title: str, body: str) -> Document:
        document = Document.objects.create(
            tenant=self.tenant,
            title=title,
            file="dummy.pdf",
            file_type=FileType.PDF,
        )
        DocumentPage.objects.create(document=document, page_number=1, content=body)

        return document

    def test_インデックス構築でチャンクが作られる(self):
        self.index.refresh_from_db()

        self.assertEqual(self.index.status, VectorIndex.Status.READY)
        self.assertEqual(self.index.chunk_count, 2)
        self.assertGreater(self.index.dimension, 0)

    def test_関連文書が上位に来る(self):
        hits = search(self.index, "結合試験の完了判定")

        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.document_id, self.relevant.pk)

    def test_除外文書は検索結果に出ない(self):
        self.relevant.status = DocumentStatus.EXCLUDED
        self.relevant.save(update_fields=["status"])

        hits = search(self.index, "結合試験の完了判定")
        document_ids = {hit.chunk.document_id for hit in hits}

        self.assertNotIn(self.relevant.pk, document_ids)

    def test_論理削除した文書は検索結果に出ない(self):
        self.relevant.soft_delete()

        hits = search(self.index, "結合試験の完了判定")

        self.assertNotIn(self.relevant.pk, {hit.chunk.document_id for hit in hits})

    def test_順位は1から連番になる(self):
        hits = search(self.index, "結合試験")

        self.assertEqual([hit.rank for hit in hits], list(range(1, len(hits) + 1)))
