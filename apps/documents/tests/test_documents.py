"""ひな型管理画面と文書アップロードの回帰テスト。

アップロードは実ファイルを書くため、MEDIA_ROOT を一時ディレクトリへ差し替える。
"""

from __future__ import annotations

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, Template

MEDIA_ROOT = tempfile.mkdtemp(prefix="verirag-test-media-")


def _pdf(name: str = "standard.pdf", body: bytes = b"%PDF-1.4 test") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


class TemplateListTests(TestCase):
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

        Template.objects.create(
            tenant=self.tenant,
            name="週次報告ひな型",
            file="templates/weekly.xlsx",
            keywords="週次 報告",
            sheet_outline=["サマリ", {"name": "課題一覧"}],
            field_mapping={"進捗率": "B4", "課題件数": "C7"},
            mapping_status=Template.MappingStatus.APPROVED,
        )
        Template.objects.create(
            tenant=self.other,
            name="他テナントのひな型",
            file="templates/other.xlsx",
        )

    def test_項目マッピングとシート構成を表示する(self):
        response = self.client.get(reverse("documents:template_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "週次報告ひな型")
        self.assertContains(response, "B4")
        self.assertContains(response, "課題一覧")

    def test_RAG対象外であることを明示する(self):
        response = self.client.get(reverse("documents:template_list"))

        self.assertContains(response, "RAG 対象外")

    def test_他テナントのひな型は表示しない(self):
        response = self.client.get(reverse("documents:template_list"))

        self.assertNotContains(response, "他テナントのひな型")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DocumentUploadTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="uploader",
            email="uploader@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)
        self.url = reverse("documents:upload")

    def test_画面が200を返す(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "文書アップロード")

    def test_検証を通れば文書が登録される(self):
        response = self.client.post(self.url, {"file": _pdf(), "title": "品質管理標準"})

        self.assertEqual(response.status_code, 200)
        document = Document.objects.get(title="品質管理標準")
        self.assertEqual(document.tenant, self.tenant)
        self.assertEqual(document.uploaded_by, self.user)
        self.assertTrue(document.sha256)

    def test_未対応形式は登録せず理由を返す(self):
        response = self.client.post(self.url, {"file": SimpleUploadedFile("memo.txt", b"hello")})

        self.assertEqual(Document.objects.count(), 0)
        self.assertContains(response, "対応していない形式です")

    def test_空ファイルは登録しない(self):
        response = self.client.post(self.url, {"file": SimpleUploadedFile("empty.pdf", b"")})

        self.assertEqual(Document.objects.count(), 0)
        self.assertContains(response, "ファイルが空です")

    def test_同一内容は警告付きで登録する(self):
        self.client.post(self.url, {"file": _pdf(), "title": "初版"})
        response = self.client.post(self.url, {"file": _pdf("copy.pdf"), "title": "複製"})

        self.assertEqual(Document.objects.count(), 2)
        self.assertContains(response, "同じ内容の文書が登録済みです")

    def test_ファイル未選択は理由を返す(self):
        response = self.client.post(self.url, {"title": "なし"})

        self.assertEqual(Document.objects.count(), 0)
        self.assertContains(response, "ファイルが選択されていません")
