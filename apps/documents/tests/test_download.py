"""原本ファイルの取得。

台帳に原本への導線が無く、本番では登録した文書を取り出す手段が存在しなかった。
根拠として登録した資料を開けないなら、AI の回答に付いた引用も確かめられない。

配信をビュー越しにしているのは、MEDIA を直接公開すると URL を知っているだけで
他テナントの文書まで読めてしまうため。ここでは分離が効いていることを固定する。
"""

from __future__ import annotations

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, FileType

MEDIA_ROOT = tempfile.mkdtemp(prefix="verirag-download-test-")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DocumentDownloadTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other = Tenant.objects.create(code="beta", name="BETA")
        self.user = self._user("owner", self.tenant, Role.PMO)
        self.document = Document.objects.create(
            tenant=self.tenant,
            title="品質管理標準",
            file_type=FileType.PDF,
            file=SimpleUploadedFile("標準.pdf", b"%PDF-1.4 body", content_type="application/pdf"),
        )
        self.client.force_login(self.user)

    def _user(self, name: str, tenant: Tenant, role: str) -> User:
        return User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="x",
            tenant=tenant,
            role=role,
        )

    def test_自テナントの文書は取得できる(self):
        response = self.client.get(reverse("documents:download", args=[self.document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 body")

    def test_日本語のファイル名でも壊れない(self):
        response = self.client.get(reverse("documents:download", args=[self.document.pk]))

        self.assertIn("filename*=UTF-8''", response["Content-Disposition"])

    def test_他テナントの文書は取得できない(self):
        self.client.force_login(self._user("outsider", self.other, Role.TENANT_ADMIN))

        response = self.client.get(reverse("documents:download", args=[self.document.pk]))

        self.assertEqual(response.status_code, 404)

    def test_未ログインではログインへ誘導する(self):
        self.client.logout()

        response = self.client.get(reverse("documents:download", args=[self.document.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_原本が無い文書は404にする(self):
        empty = Document.objects.create(
            tenant=self.tenant, title="原本のない台帳行", file_type=FileType.PDF
        )

        response = self.client.get(reverse("documents:download", args=[empty.pk]))

        self.assertEqual(response.status_code, 404)

    def test_保存先から消えていても500にしない(self):
        """台帳に残っているのにファイルが無い、という差がそのまま出る形にする。"""

        self.document.file.storage.delete(self.document.file.name)

        response = self.client.get(reverse("documents:download", args=[self.document.pk]))

        self.assertEqual(response.status_code, 404)

    def test_一覧から原本へ辿れる(self):
        response = self.client.get(reverse("documents:list"))

        self.assertContains(response, reverse("documents:download", args=[self.document.pk]))
