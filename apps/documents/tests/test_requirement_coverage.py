"""テスト計画と要件の整合性チェック（要件 #44）。

「要件書が無いのにカバー率100%」を出さないことが最重要。
登録漏れを達成として表示すると、この表が逆に危険になる。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, DocumentPage, DocumentStatus, FileType
from apps.documents.services.requirement_coverage import build_coverage_report
from apps.projects.models import Project, ProjectMember


class RequirementCoverageTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.documents = Document.objects.filter(tenant=self.tenant)

    def _document(self, title: str, content: str = "", *, status=DocumentStatus.ACTIVE) -> Document:
        document = Document.objects.create(
            tenant=self.tenant,
            title=title,
            file=f"documents/{title}.txt",
            file_type=FileType.TXT,
            status=status,
        )

        if content:
            DocumentPage.objects.create(document=document, page_number=1, content=content)

        return document

    def test_要件書とテスト計画書の要件IDを突き合わせる(self) -> None:
        self._document("要件定義書", "REQ-AG-001 と REQ-AG-002 を満たすこと。")
        self._document("テスト計画書", "REQ-AG-001 の確認手順。")

        report = build_coverage_report(self.documents)

        self.assertTrue(report.determinable)
        self.assertEqual(report.requirement_total, 2)
        self.assertEqual(report.covered_total, 1)
        self.assertEqual(report.coverage_percent, 50)
        self.assertEqual([row.requirement_id for row in report.uncovered], ["REQ-AG-002"])

    def test_要件書に無いテストを出所不明として挙げる(self) -> None:
        self._document("要件定義書", "REQ-AG-001")
        self._document("テスト計画書", "REQ-AG-001 と REQ-XX-999")

        report = build_coverage_report(self.documents)

        self.assertEqual([row.requirement_id for row in report.orphan_tests], ["REQ-XX-999"])
        self.assertEqual(report.tone, "a")

    def test_要件書が無ければ判定不能とする(self) -> None:
        self._document("テスト計画書", "REQ-AG-001")

        report = build_coverage_report(self.documents)

        self.assertFalse(report.determinable)
        self.assertIn("要件書が登録されていない", report.summary)

    def test_本文が取れていない文書は判定に含めず理由を残す(self) -> None:
        self._document("要件定義書")

        report = build_coverage_report(self.documents)

        self.assertFalse(report.determinable)
        self.assertEqual(len(report.unreadable_documents), 1)
        self.assertIn("本文抽出", report.summary)

    def test_全要件にテストがあれば緑にする(self) -> None:
        self._document("要件定義書", "REQ-AG-001 REQ-AG-002")
        self._document("結合試験仕様書", "REQ-AG-001 REQ-AG-002")

        report = build_coverage_report(self.documents)

        self.assertEqual(report.coverage_percent, 100)
        self.assertEqual(report.tone, "g")

    def test_大文字小文字の違いを同じIDとして扱う(self) -> None:
        self._document("要件定義書", "req-ag-001")
        self._document("テスト計画書", "REQ-AG-001")

        report = build_coverage_report(self.documents)

        self.assertEqual(report.coverage_percent, 100)

    def test_章番号を要件IDと誤認しない(self) -> None:
        self._document("要件定義書", "1.2 の章に REQ-AG-001 を記載。3.4 も参照。")

        report = build_coverage_report(self.documents)

        self.assertEqual([row.requirement_id for row in report.rows], ["REQ-AG-001"])

    def test_RAG対象外の文書は見ない(self) -> None:
        self._document("要件定義書", "REQ-AG-001")
        self._document("旧テスト計画書", "REQ-AG-001", status=DocumentStatus.EXCLUDED)

        report = build_coverage_report(self.documents)

        self.assertEqual(report.covered_total, 0)


class RequirementCoverageViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["tenant_id"] = str(self.tenant.pk)
        session.save()

        project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        ProjectMember.objects.create(project=project, user=self.user, role_label="PMO")

    def test_品質画面に整合性の結果が出る(self) -> None:
        document = Document.objects.create(
            tenant=self.tenant,
            title="要件定義書",
            file="documents/req.txt",
            file_type=FileType.TXT,
        )
        DocumentPage.objects.create(document=document, page_number=1, content="REQ-AG-001")

        response = self.client.get(reverse("dashboard:quality"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "テスト計画と要件の整合性")
        self.assertContains(response, "REQ-AG-001")

    def test_文書が無くても画面が壊れない(self) -> None:
        response = self.client.get(reverse("dashboard:quality"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "要件書が登録されていないため判定できません")
