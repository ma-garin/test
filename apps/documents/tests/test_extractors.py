"""本文抽出（PDF/Office/テキスト）の回帰テスト。

外部ネットワークにも外部ライブラリにも依存しない経路（.txt / .md）で、
「抽出 → チャンク化 → 検索」が端から端まで通ることを確認する。
未導入ライブラリが必要な形式は `sys.modules` に None を差し込んで、
環境に依存せず「未導入」を再現する。
"""

from __future__ import annotations

import sys
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import (
    Document,
    DocumentPage,
    DocumentStatus,
    FileType,
    IngestJob,
)
from apps.documents.services import extractors
from apps.rag.models import Chunk, VectorIndex
from apps.rag.services.retriever import search

MEDIA_ROOT = tempfile.mkdtemp(prefix="verirag-test-extract-")

BODY = (
    "工程遅延の是正計画は月次で見直す。\n"
    "品質指標のレビューは各フェーズ完了時に実施する。\n"
)


def _upload(name: str, body: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="text/plain")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class TextExtractionTests(TestCase):
    """外部依存ゼロの経路。ここが通らないと他形式の失敗と切り分けられない。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")

    def _document(self, name: str, body: bytes, file_type: str = FileType.TXT) -> Document:
        return Document.objects.create(
            tenant=self.tenant,
            title=name,
            file=_upload(name, body),
            file_type=file_type,
            file_size=len(body),
        )

    def test_テキストが抽出されチャンク化され検索で見つかる(self):
        document = self._document("standard.txt", BODY.encode("utf-8"))

        result = extractors.ingest(document)

        self.assertTrue(result.succeeded, result.message)
        self.assertEqual(result.page_count, 1)
        self.assertGreater(result.char_count, 0)

        page = DocumentPage.objects.get(document=document)
        self.assertIn("是正計画", page.content)

        index = VectorIndex.objects.get(tenant=self.tenant, project__isnull=True)
        self.assertGreater(Chunk.objects.filter(index=index).count(), 0)

        hits = search(index, "是正計画")
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.document_id, document.pk)

    def test_Markdownも同じ経路で抽出できる(self):
        document = self._document("guide.md", ("# 手順\n" + BODY).encode("utf-8"), FileType.MD)

        result = extractors.ingest(document)

        self.assertTrue(result.succeeded, result.message)
        self.assertEqual(DocumentPage.objects.filter(document=document).count(), 1)

    def test_統計に文字数と抽出元ページ数を残す(self):
        document = self._document("stats.txt", BODY.encode("utf-8"))

        result = extractors.ingest(document)

        stats = result.job.stats
        self.assertEqual(stats["pages"], 1)
        self.assertEqual(stats["source_pages"], 1)
        self.assertEqual(stats["characters"], len(BODY.strip()))

    def test_大きなテキストは複数ページへ分割される(self):
        body = ("あ" * 100 + "\n") * 100

        document = self._document("large.txt", body.encode("utf-8"))
        result = extractors.ingest(document)

        self.assertTrue(result.succeeded, result.message)
        self.assertGreater(result.page_count, 1)

    def test_復号できないバイト列でも例外を投げない(self):
        document = self._document("broken.txt", b"\xff\xfe\xfa test")

        result = extractors.ingest(document)

        self.assertTrue(result.succeeded, result.message)

    def test_再抽出でページが重複しない(self):
        document = self._document("again.txt", BODY.encode("utf-8"))

        extractors.ingest(document)
        extractors.ingest(document)

        self.assertEqual(DocumentPage.objects.filter(document=document).count(), 1)

    def test_本文が0文字なら失敗として記録する(self):
        document = self._document("empty.txt", b"   \n \n")

        result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("1文字も抽出できませんでした", result.message)
        self.assertEqual(DocumentPage.objects.filter(document=document).count(), 0)

        document.refresh_from_db()
        # 空のまま検索対象に残さない（rebuild_index の対象から外す）。
        self.assertEqual(document.status, DocumentStatus.ERROR)

    def test_抽出中の想定外例外はジョブの失敗になる(self):
        document = self._document("corrupt.txt", BODY.encode("utf-8"))

        def boom(_document):
            raise RuntimeError("file is not a zip file")

        with mock.patch.dict(extractors.EXTRACTORS, {FileType.TXT: boom}):
            result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("想定外のエラー", result.message)
        self.assertEqual(result.job.status, IngestJob.Status.FAILED)

    def test_ファイル実体が無い文書は失敗として記録する(self):
        document = Document.objects.create(
            tenant=self.tenant,
            title="missing",
            file="documents/acme/_global/missing.txt",
            file_type=FileType.TXT,
        )

        result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("ファイルを開けません", result.message)

    def test_旧形式は理由を明示して失敗する(self):
        document = self._document("old.doc", b"legacy binary", FileType.DOC)

        result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("旧形式", result.message)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class MissingDependencyTests(TestCase):
    """未導入ライブラリが必要な形式でも 500 にせず、理由を残す。"""

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

    def _document(self, name: str, file_type: str) -> Document:
        return Document.objects.create(
            tenant=self.tenant,
            title=name,
            file=_upload(name, b"dummy-binary"),
            file_type=file_type,
        )

    def test_pypdf未導入ならインストール方法を残す(self):
        document = self._document("plan.pdf", FileType.PDF)

        with mock.patch.dict(sys.modules, {"pypdf": None}):
            result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("pypdf", result.message)
        self.assertIn("インストール", result.message)

    def test_python_docx未導入でも画面は500にならない(self):
        document = self._document("report.docx", FileType.DOCX)

        with mock.patch.dict(sys.modules, {"docx": None}):
            result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("python-docx", result.message)

        response = self.client.get(reverse("documents:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "抽出失敗")
        self.assertContains(response, "python-docx")

    def test_openpyxl未導入ならExcelは失敗として記録する(self):
        document = self._document("wbs.xlsx", FileType.XLSX)

        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("openpyxl", result.message)

    def test_python_pptx未導入ならPowerPointは失敗として記録する(self):
        document = self._document("kickoff.pptx", FileType.PPTX)

        with mock.patch.dict(sys.modules, {"pptx": None}):
            result = extractors.ingest(document)

        self.assertFalse(result.succeeded)
        self.assertIn("python-pptx", result.message)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ExtractViewTests(TestCase):
    """台帳からの抽出実行。テナント分離を入口で切る。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other = Tenant.objects.create(code="beta", name="BETA")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)
        self.document = Document.objects.create(
            tenant=self.tenant,
            title="品質標準",
            file=_upload("quality.txt", BODY.encode("utf-8")),
            file_type=FileType.TXT,
        )

    def test_抽出を実行すると台帳へ戻り抽出済みになる(self):
        response = self.client.post(
            reverse("documents:extract", args=[self.document.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "抽出済み")
        self.assertEqual(DocumentPage.objects.filter(document=self.document).count(), 1)

    def test_他テナントの文書は抽出できない(self):
        foreign = Document.objects.create(
            tenant=self.other,
            title="他テナント文書",
            file=_upload("foreign.txt", BODY.encode("utf-8")),
            file_type=FileType.TXT,
        )

        response = self.client.post(reverse("documents:extract", args=[foreign.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(DocumentPage.objects.filter(document=foreign).count(), 0)

    def test_未ログインは実行できない(self):
        self.client.logout()

        response = self.client.post(reverse("documents:extract", args=[self.document.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(DocumentPage.objects.filter(document=self.document).count(), 0)
