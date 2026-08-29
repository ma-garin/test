"""CSV 取込の検証。

「1行でもエラーがあれば何も入らない」「手入力を黙って上書きしない」
「Excel の Shift_JIS を読める」の3点は、現場が使えるかどうかを分ける。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.accounts.constants import Role
from apps.performance.constants import FigureSource, ImportKind, ImportStatus
from apps.performance.models import ActualFigure, OrgUnit
from apps.performance.services import csv_io
from apps.performance.tests import factories

HEADER = "org_code,employee_code,month,revenue,gross_profit,operating_profit,note\n"


class CsvImportTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.user = factories.make_user(self.tenant, "manager@example.com", role=Role.TENANT_ADMIN)
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant, manager=self.user)
        self.member = factories.make_member(self.tenant, self.units["sec"])

    def _context(self, **kwargs):
        return csv_io.ImportContext(
            tenant=self.tenant,
            user=self.user,
            fiscal_year=self.year,
            editable_org_ids={unit.pk for unit in self.units.values()},
            **kwargs,
        )

    def _run(self, body: str, *, skip_errors: bool = False, context=None, encoding="utf-8"):
        return csv_io.run_import(
            kind=ImportKind.ACTUAL_FIGURE,
            raw=body.encode(encoding),
            filename="actual.csv",
            context=context or self._context(),
            skip_errors=skip_errors,
        )

    def test_imports_org_and_member_rows(self) -> None:
        outcome, batch = self._run(
            HEADER
            + "sec,,2026-04,1000,250,100,\n"
            + f"sec,{self.member.employee_code},2026-04,600,150,60,個人配分\n"
        )

        self.assertEqual(outcome.created, 2)
        self.assertEqual(batch.status, ImportStatus.APPLIED)
        self.assertEqual(
            ActualFigure.objects.get(org_unit=self.units["sec"], member__isnull=True).revenue,
            Decimal("1000"),
        )
        self.assertEqual(
            ActualFigure.objects.get(member=self.member).revenue, Decimal("600")
        )

    def test_rejects_whole_file_when_a_row_fails(self) -> None:
        outcome, batch = self._run(
            HEADER + "sec,,2026-04,1000,250,100,\n" + "unknown,,2026-04,500,100,50,\n"
        )

        self.assertFalse(outcome.applied)
        self.assertEqual(batch.status, ImportStatus.REJECTED)
        self.assertEqual(ActualFigure.objects.count(), 0)
        self.assertIn("組織コード", batch.errors[0]["message"])
        self.assertEqual(batch.errors[0]["line"], 3)

    def test_skip_errors_applies_valid_rows_only(self) -> None:
        outcome, batch = self._run(
            HEADER + "sec,,2026-04,1000,250,100,\n" + "unknown,,2026-04,500,100,50,\n",
            skip_errors=True,
        )

        self.assertTrue(outcome.applied)
        self.assertEqual(batch.status, ImportStatus.PARTIAL)
        self.assertEqual(ActualFigure.objects.count(), 1)

    def test_month_outside_fiscal_year_is_an_error(self) -> None:
        _, batch = self._run(HEADER + "sec,,2027-05,1000,250,100,\n")

        self.assertIn("年度の範囲外", batch.errors[0]["message"])

    def test_org_outside_edit_scope_is_rejected(self) -> None:
        context = csv_io.ImportContext(
            tenant=self.tenant,
            user=self.user,
            fiscal_year=self.year,
            editable_org_ids={self.units["prj"].pk},
        )

        _, batch = self._run(HEADER + "sec,,2026-04,1000,250,100,\n", context=context)

        self.assertIn("権限", batch.errors[0]["message"])

    def test_manual_values_are_protected_by_default(self) -> None:
        factories.add_actual(
            self.year, self.units["sec"], date(2026, 4, 1), 999, source=FigureSource.MANUAL
        )

        outcome, _ = self._run(HEADER + "sec,,2026-04,1000,250,100,\n")

        self.assertEqual(outcome.protected, 1)
        self.assertEqual(ActualFigure.objects.get().revenue, Decimal("999"))

    def test_manual_values_are_overwritten_when_asked(self) -> None:
        factories.add_actual(
            self.year, self.units["sec"], date(2026, 4, 1), 999, source=FigureSource.MANUAL
        )

        outcome, _ = self._run(
            HEADER + "sec,,2026-04,1000,250,100,\n",
            context=self._context(overwrite_manual=True),
        )

        self.assertEqual(outcome.updated, 1)
        self.assertEqual(ActualFigure.objects.get().revenue, Decimal("1000"))

    def test_reads_shift_jis_and_formatted_numbers(self) -> None:
        outcome, _ = self._run(
            HEADER + 'sec,,2026/4,"1,000",250,100,四月分\n', encoding="cp932"
        )

        self.assertTrue(outcome.applied)
        figure = ActualFigure.objects.get()
        self.assertEqual(figure.revenue, Decimal("1000"))
        self.assertEqual(figure.month, date(2026, 4, 1))
        self.assertEqual(figure.note, "四月分")

    def test_missing_required_column_is_a_file_error(self) -> None:
        with self.assertRaises(csv_io.CsvFormatError):
            self._run("org_code,revenue\nsec,1000\n")

    def test_org_master_import_resolves_parents_within_the_file(self) -> None:
        body = (
            "code,name,level,parent_code,manager_email,sort_order\n"
            "new-div,新設部,division,,,10\n"
            "new-sec,新設課,section,new-div,,10\n"
        )
        outcome, _ = csv_io.run_import(
            kind=ImportKind.ORG_UNIT,
            raw=body.encode("utf-8"),
            filename="orgs.csv",
            context=self._context(),
        )

        self.assertEqual(outcome.created, 2)
        self.assertEqual(
            OrgUnit.objects.get(code="new-sec").parent, OrgUnit.objects.get(code="new-div")
        )

    def test_org_master_cannot_update_units_outside_edit_scope(self) -> None:
        context = csv_io.ImportContext(
            tenant=self.tenant,
            user=self.user,
            fiscal_year=self.year,
            editable_org_ids={self.units["prj"].pk},
        )
        body = (
            "code,name,level,parent_code,manager_email,sort_order\n"
            "sec,乗っ取り課,section,div,,10\n"
        )
        _, batch = csv_io.run_import(
            kind=ImportKind.ORG_UNIT,
            raw=body.encode("utf-8"),
            filename="orgs.csv",
            context=context,
        )

        self.units["sec"].refresh_from_db()
        self.assertEqual(batch.status, ImportStatus.REJECTED)
        self.assertIn("権限", batch.errors[0]["message"])
        self.assertNotEqual(self.units["sec"].name, "乗っ取り課")

    def test_org_master_rejects_level_mismatch(self) -> None:
        body = (
            "code,name,level,parent_code,manager_email,sort_order\n"
            "bad-prj,飛び級,project,div,,10\n"
        )
        _, batch = csv_io.run_import(
            kind=ImportKind.ORG_UNIT,
            raw=body.encode("utf-8"),
            filename="orgs.csv",
            context=self._context(),
        )

        self.assertEqual(batch.status, ImportStatus.REJECTED)
        self.assertFalse(OrgUnit.objects.filter(code="bad-prj").exists())


class CsvExportTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant)

    def test_export_matches_import_columns(self) -> None:
        factories.add_actual(self.year, self.units["sec"], date(2026, 4, 1), 1000)

        body = csv_io.export_csv(ImportKind.ACTUAL_FIGURE, fiscal_year=self.year)
        lines = body.strip().splitlines()

        self.assertEqual(lines[0], ",".join(csv_io.COLUMNS[ImportKind.ACTUAL_FIGURE]))
        self.assertIn("sec,,2026-04,1000", lines[1])

    def test_template_has_header_and_samples(self) -> None:
        body = csv_io.template_csv(ImportKind.PLAN_FIGURE)

        self.assertTrue(body.startswith("org_code,employee_code,month"))
        self.assertGreater(len(body.strip().splitlines()), 1)
