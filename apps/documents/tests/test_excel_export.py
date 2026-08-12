"""Excel 成果物出力の回帰テスト。

この環境には openpyxl が入っていない。そのため

- ライブラリ未導入でも画面が 500 にならないこと
- 書き込み計画（何を出せて何を出せないか）はライブラリ無しでも作れること

を実際の未導入状態で確かめ、書き込み経路は openpyxl の代役（スタブ）を差し込んで
検証する。スタブなら「元のひな型ファイルへ保存していないか」も直接見られる。
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Template
from apps.documents.services import excel_export
from apps.pmo.models import Deliverable
from apps.projects.models import Project

MEDIA_ROOT = tempfile.mkdtemp(prefix="verirag-test-excel-")

#: ひな型ファイルの中身。実体が Excel でなくても、上書きされたかどうかは判定できる。
TEMPLATE_BYTES = b"original-template-bytes"


class _FakeCell:
    def __init__(self, value=None) -> None:
        self.value = value


class _FakeSheet:
    def __init__(self, title: str) -> None:
        self.title = title
        self.cells: dict[str, _FakeCell] = {}

    def __getitem__(self, coordinate: str) -> _FakeCell:
        return self.cells.setdefault(coordinate, _FakeCell())


class _FakeWorkbook:
    def __init__(self, sheet_names: list[str]) -> None:
        self.sheetnames = list(sheet_names)
        self.sheets = {name: _FakeSheet(name) for name in sheet_names}
        self.save_targets: list[object] = []

    @property
    def active(self) -> _FakeSheet:
        return self.sheets[self.sheetnames[0]]

    def __getitem__(self, name: str) -> _FakeSheet:
        return self.sheets[name]

    def save(self, target) -> None:
        # 保存先を記録する。パス（＝原本上書き）で呼ばれていないことを検証するため。
        self.save_targets.append(target)
        target.write(b"generated-xlsx")


class _FakeOpenpyxl:
    def __init__(self, workbook: _FakeWorkbook) -> None:
        self.workbook = workbook
        self.load_args: list[tuple[object, bool]] = []

    def load_workbook(self, source, keep_vba: bool = False) -> _FakeWorkbook:
        self.load_args.append((source, keep_vba))

        return self.workbook


def _template(tenant: Tenant, **kwargs) -> Template:
    defaults = {
        "name": "週次報告ひな型",
        "file": SimpleUploadedFile("weekly.xlsx", TEMPLATE_BYTES),
        "field_mapping": {"進捗率": "B4", "課題件数": "C7"},
        "mapping_status": Template.MappingStatus.APPROVED,
    }
    defaults.update(kwargs)

    return Template.objects.create(tenant=tenant, **defaults)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ExportPlanTests(TestCase):
    """書き込み計画。openpyxl の有無に関わらず成立すること。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(
            tenant=self.tenant,
            code="acme-core",
            name="基幹刷新",
            progress_percent=42,
            project_manager="山田",
        )

    def test_マッピングに無い項目を未出力として報告する(self):
        template = _template(self.tenant, field_mapping={"進捗率": "B4"})

        plan = excel_export.build_plan(template, project=self.project)

        self.assertEqual([item.field_name for item in plan.writes], ["進捗率"])
        unmapped = {
            item.field_name
            for item in plan.skipped
            if item.reason == excel_export.REASON_NO_MAPPING
        }
        # 値は取れているのに書き込み先が無い項目。黙って落とすと出力漏れになる。
        self.assertIn("案件名", unmapped)
        self.assertIn("PM", unmapped)

    def test_値の取れない項目名を理由付きで報告する(self):
        template = _template(self.tenant, field_mapping={"宇宙項目": "Z9", "進捗率": "こわれた"})

        plan = excel_export.build_plan(template, project=self.project)

        reasons = {item.field_name: item.reason for item in plan.skipped}
        self.assertEqual(reasons["宇宙項目"], excel_export.REASON_UNKNOWN_FIELD)
        self.assertEqual(reasons["進捗率"], excel_export.REASON_BAD_CELL)

    def test_項目名の表記揺れを吸収する(self):
        template = _template(self.tenant, field_mapping={"プロジェクト名": "A1", "課題件数": "C7"})

        plan = excel_export.build_plan(template, project=self.project)

        written = {item.field_name: item.value for item in plan.writes}
        self.assertEqual(written["プロジェクト名"], "基幹刷新")
        self.assertEqual(written["課題件数"], 0)

    def test_成果物の内容を書き込み対象にする(self):
        deliverable = Deliverable.objects.create(
            project=self.project,
            title="第3週 週次報告",
            body="今週の状況",
        )
        template = _template(self.tenant, field_mapping={"タイトル": "A1", "本文": "A5"})

        plan = excel_export.build_plan(template, project=self.project, deliverable=deliverable)

        written = {item.field_name: item.value for item in plan.writes}
        self.assertEqual(written["タイトル"], "第3週 週次報告")
        self.assertEqual(written["本文"], "今週の状況")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ExportRenderTests(TestCase):
    """書き込み経路。openpyxl の代役を差し込んで検証する。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(
            tenant=self.tenant, code="acme-core", name="基幹刷新", progress_percent=42
        )
        self.template = _template(self.tenant, field_mapping={"進捗率": "B4", "案件名": "サマリ!A1"})
        self.workbook = _FakeWorkbook(["サマリ", "課題一覧"])

    def _export(self):
        fake = _FakeOpenpyxl(self.workbook)

        with patch.object(excel_export, "_load_openpyxl", return_value=fake):
            return excel_export.export(self.template, project=self.project), fake

    def test_指定セルへ値を書き込む(self):
        result, _ = self._export()

        self.assertTrue(result.ok)
        self.assertEqual(self.workbook.sheets["サマリ"].cells["A1"].value, "基幹刷新")
        self.assertEqual(self.workbook.sheets["サマリ"].cells["B4"].value, 42.0)
        self.assertEqual(result.content, b"generated-xlsx")

    def test_元のひな型ファイルを変更しない(self):
        path = Path(self.template.file.path)
        before = path.read_bytes()

        result, _ = self._export()

        self.assertTrue(result.ok)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(before, TEMPLATE_BYTES)
        # 保存先はメモリ上のバッファだけ。ファイルパスへ save() する経路を持たない。
        self.assertTrue(all(isinstance(t, io.BytesIO) for t in self.workbook.save_targets))

    def test_数式セルは上書きせず理由を返す(self):
        self.workbook.sheets["サマリ"].cells["B4"] = _FakeCell("=SUM(C1:C9)")

        result, _ = self._export()

        self.assertEqual(self.workbook.sheets["サマリ"].cells["B4"].value, "=SUM(C1:C9)")
        reasons = {item.field_name: item.reason for item in result.skipped}
        self.assertEqual(reasons["進捗率"], excel_export.REASON_FORMULA)

    def test_ひな型に無いシートは書き込まず理由を返す(self):
        self.template.field_mapping = {"案件名": "存在しないシート!A1"}
        self.template.save(update_fields=["field_mapping"])

        result, _ = self._export()

        reasons = {item.field_name: item.reason for item in result.skipped}
        self.assertEqual(reasons["案件名"], excel_export.REASON_NO_SHEET)

    def test_ひな型ファイルが無ければ例外にせず理由を返す(self):
        Path(self.template.file.path).unlink()

        result, _ = self._export()

        self.assertFalse(result.ok)
        self.assertIn("見つかりません", result.message)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class TemplateExportViewTests(TestCase):
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
        self.project = Project.objects.create(
            tenant=self.tenant, code="acme-core", name="基幹刷新", progress_percent=42
        )
        self.template = _template(self.tenant)
        self.other_template = _template(self.other, name="他テナントのひな型")
        self.client.force_login(self.user)

    def _url(self, template: Template, **params) -> str:
        url = reverse("documents:template_export", args=[template.pk])
        query = "&".join(f"{key}={value}" for key, value in params.items())

        return f"{url}?{query}" if query else url

    def test_openpyxl未導入でも500にせず理由を出す(self):
        error = excel_export.ExcelExportError("Excel 出力には openpyxl が必要です")

        with patch.object(excel_export, "_load_openpyxl", side_effect=error):
            response = self.client.get(self._url(self.template, project=self.project.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "openpyxl")
        self.assertFalse(response.context["result"].ok)

    def test_未出力の項目を画面に必ず出す(self):
        fake = _FakeOpenpyxl(_FakeWorkbook(["サマリ"]))

        with patch.object(excel_export, "_load_openpyxl", return_value=fake):
            response = self.client.get(self._url(self.template, project=self.project.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未出力の項目")
        self.assertTrue(response.context["result"].has_skipped)

    def test_ダウンロード指定でExcelを返す(self):
        fake = _FakeOpenpyxl(_FakeWorkbook(["サマリ"]))

        with patch.object(excel_export, "_load_openpyxl", return_value=fake):
            response = self.client.get(
                self._url(self.template, project=self.project.pk, download=1)
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], excel_export.XLSX_CONTENT_TYPE)
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response.content, b"generated-xlsx")

    def test_他テナントのひな型は出力できない(self):
        response = self.client.get(self._url(self.other_template))

        self.assertEqual(response.status_code, 404)

    def test_未ログインならログイン画面へ送る(self):
        self.client.logout()

        response = self.client.get(self._url(self.template))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
