"""LDF-05: テスト証跡の CSV 取込。

受入条件「課題・不具合・テストが予測用 Signal として、時刻・URL つきで入る」の
テスト側を確認する。1 行の不備で全体を落とさないこと、未対応値を黙って寄せないこと。
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.accounts.models import Tenant
from apps.forecast.models import Signal, SignalClassification, SignalSource, TestEvidence
from apps.forecast.services.test_evidence_import import import_test_evidence
from apps.projects.models import Project

HEADER = "external_id,name,kind,result,executed_at,environment,failure_reason,external_url,retest_planned_on"


class TestEvidenceImportTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")

    def _csv(self, *rows: str) -> str:
        return "\n".join((HEADER, *rows))

    def test_rows_are_imported_with_time_and_url(self):
        report = import_test_evidence(
            self.project,
            self._csv(
                "TC-1,受注登録の正常系,system,failed,2026-08-11T10:30:00,stg,金額がずれる,"
                "https://example.invalid/tc/1,2026-08-14"
            ),
        )
        self.assertEqual(report.created, 1)
        evidence = TestEvidence.objects.get(external_id="TC-1")
        self.assertEqual(evidence.result, TestEvidence.Result.FAILED)
        self.assertEqual(evidence.retest_planned_on, date(2026, 8, 14))
        self.assertEqual(evidence.external_url, "https://example.invalid/tc/1")

    def test_failed_test_becomes_a_signal(self):
        import_test_evidence(
            self.project,
            self._csv("TC-1,受注登録,system,failed,2026-08-11T10:30:00,stg,再現,,"),
        )
        signal = Signal.objects.get(external_id__startswith="TC-1@")
        self.assertEqual(signal.source, SignalSource.TEST_MANAGEMENT)
        self.assertEqual(signal.classification, SignalClassification.TEST_FAILED)
        self.assertIsNotNone(signal.occurred_at)

    def test_passed_test_is_classified_separately(self):
        import_test_evidence(
            self.project, self._csv("TC-2,受注登録,system,passed,2026-08-11T10:30:00,stg,,,")
        )
        signal = Signal.objects.get(external_id__startswith="TC-2@")
        self.assertEqual(signal.classification, SignalClassification.TEST_PASSED)

    def test_reimport_updates_instead_of_duplicating(self):
        row = "TC-1,受注登録,system,failed,2026-08-11T10:30:00,stg,,,"
        import_test_evidence(self.project, self._csv(row))
        report = import_test_evidence(
            self.project,
            self._csv("TC-1,受注登録,system,passed,2026-08-12T10:30:00,stg,,,"),
        )
        self.assertEqual(report.updated, 1)
        self.assertEqual(TestEvidence.objects.count(), 1)
        self.assertEqual(TestEvidence.objects.get().result, TestEvidence.Result.PASSED)

    def test_bad_row_does_not_stop_the_others(self):
        report = import_test_evidence(
            self.project,
            self._csv(
                "TC-1,正常な行,system,failed,2026-08-11T10:30:00,,,,",
                "TC-2,壊れた行,system,failed,日付ではない,,,,",
                "TC-3,もう一つ正常な行,unit,passed,2026-08-11T11:00:00,,,,",
            ),
        )
        self.assertEqual(report.created, 2)
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(report.errors[0].external_id, "TC-2")

    def test_unknown_result_is_rejected_not_defaulted(self):
        report = import_test_evidence(
            self.project, self._csv("TC-9,不明な結果,system,maybe,2026-08-11T10:30:00,,,,")
        )
        self.assertEqual(report.created, 0)
        self.assertIn("未対応の値", report.errors[0].reason)
        self.assertFalse(TestEvidence.objects.exists())

    def test_missing_required_column_is_reported(self):
        report = import_test_evidence(self.project, "external_id,name\nTC-1,テスト")
        self.assertTrue(report.has_errors)
        self.assertIn("必須列がありません", report.errors[0].reason)

    def test_empty_external_id_is_rejected(self):
        report = import_test_evidence(
            self.project, self._csv(",名前なし,system,failed,2026-08-11T10:30:00,,,,")
        )
        self.assertIn("external_id", report.errors[0].reason)

    def test_failure_without_retest_blocks_completion(self):
        import_test_evidence(
            self.project, self._csv("TC-1,再試験未定,system,failed,2026-08-11T10:30:00,,,,")
        )
        self.assertTrue(TestEvidence.objects.get().blocks_completion)

    def test_failure_with_retest_does_not_block(self):
        import_test_evidence(
            self.project,
            self._csv("TC-1,再試験あり,system,failed,2026-08-11T10:30:00,,,,2026-08-14"),
        )
        self.assertFalse(TestEvidence.objects.get().blocks_completion)

    def test_summary_line_reports_the_breakdown(self):
        report = import_test_evidence(
            self.project,
            self._csv(
                "TC-1,ok,system,failed,2026-08-11T10:30:00,,,,",
                "TC-2,ng,system,maybe,2026-08-11T10:30:00,,,,",
            ),
        )
        self.assertIn("新規 1件", report.summary_line())
        self.assertIn("取込不可 1件", report.summary_line())

    def test_evidence_is_scoped_to_the_project(self):
        other = Project.objects.create(tenant=self.tenant, code="p2", name="案件2")
        import_test_evidence(
            self.project, self._csv("TC-1,同じID,system,failed,2026-08-11T10:30:00,,,,")
        )
        import_test_evidence(
            other, self._csv("TC-1,同じID,system,passed,2026-08-11T10:30:00,,,,")
        )
        self.assertEqual(TestEvidence.objects.count(), 2)
