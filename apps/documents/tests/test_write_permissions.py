"""文書の登録・抽出に対する権限テスト。

登録も抽出も「原本と本文を増やす」書き込みなので、参照専用ロールから直接
POST して 403 になること、かつ Document が 1 件も増えないことを対で確かめる。
アップロードは実ファイルを書くため、MEDIA_ROOT を一時ディレクトリへ差し替える。
"""

from __future__ import annotations

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, DocumentPage, FileType
from apps.projects.models import Project, ProjectMember

MEDIA_ROOT = tempfile.mkdtemp(prefix="verirag-test-permission-media-")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DocumentWritePermissionTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

        self.viewer = self._member("viewer", Role.VIEWER, ProjectRole.VIEWER)
        self.member = self._member("member", Role.CHANGE_MANAGER, ProjectRole.MEMBER)

        self.document = Document.objects.create(
            tenant=self.tenant,
            project=self.project,
            title="既存の文書",
            file="dummy.pdf",
            file_type=FileType.PDF,
        )

    def _member(self, name: str, role: str, project_role: str) -> User:
        user = User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="test-password",
            tenant=self.tenant,
            role=role,
        )
        ProjectMember.objects.create(project=self.project, user=user, role=project_role)

        return user

    def _payload(self) -> dict:
        return {
            "title": "権限テストで登録した文書",
            "project": str(self.project.pk),
            "source_note": "権限テスト",
            "file": SimpleUploadedFile(
                "permission.pdf", b"%PDF-1.4 permission test", content_type="application/pdf"
            ),
        }

    def test_参照専用ロールは文書を登録できず件数も増えない(self):
        self.client.force_login(self.viewer)

        before = Document.objects.count()
        response = self.client.post(reverse("documents:upload"), self._payload())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Document.objects.count(), before)

    def test_参照専用ロールでもアップロード画面自体は開ける(self):
        self.client.force_login(self.viewer)

        self.assertEqual(self.client.get(reverse("documents:upload")).status_code, 200)

    def test_参照専用ロールは本文抽出を実行できない(self):
        self.client.force_login(self.viewer)

        before = DocumentPage.objects.count()
        response = self.client.post(
            reverse("documents:extract", args=[self.document.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(DocumentPage.objects.count(), before)

    def test_編集できる立場なら文書を登録できる(self):
        """締めすぎていないこと。登録は編集権限で行える。"""

        self.client.force_login(self.member)

        response = self.client.post(reverse("documents:upload"), self._payload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Document.objects.filter(title="権限テストで登録した文書").exists())
