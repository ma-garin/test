"""まれな状況で落ちない・数字を誤らないこと。

ODC の分類でいう Rare Situation（0 件、NULL、まれな組み合わせ）に当たるもの。
実データに近い状態で流すシステムテストではほぼ通ってしまう型なので、
ここで個別に固定する。
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.dashboard.models import Alert
from apps.dashboard.services.detection import runner
from apps.dashboard.services.detection.findings import SkipReason
from apps.dashboard.services.overview import build_overview
from apps.dashboard.services.poc_evaluation import business_days_between
from apps.projects.models import Project, WbsTask


class BusinessDaysTests(TestCase):
    def test_土日を除いて数える(self):
        # 2026-08-17(月) 〜 2026-08-21(金)
        self.assertEqual(business_days_between(date(2026, 8, 17), date(2026, 8, 21)), 4)

    def test_週をまたいでも正しい(self):
        self.assertEqual(business_days_between(date(2026, 8, 17), date(2026, 8, 31)), 10)

    def test_逆順は負の値になる(self):
        self.assertEqual(business_days_between(date(2026, 8, 21), date(2026, 8, 17)), -4)

    def test_同じ日は0(self):
        self.assertEqual(business_days_between(date(2026, 8, 17), date(2026, 8, 17)), 0)

    def test_極端に離れた日付でも即座に返る(self):
        """1 日ずつ回す実装だと、日付の入力誤りでリクエストが事実上止まっていた。"""

        import time

        started = time.perf_counter()
        result = business_days_between(date(1900, 1, 1), date(2026, 8, 15))

        self.assertGreater(result, 0)
        self.assertLess(time.perf_counter() - started, 0.5)


class DetectionResilienceTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件")

    def test_検知器が1つ落ちても他は動く(self):
        """危険を見つける機能が、1 つの例外で画面ごと落ちてはいけない。"""

        with mock.patch(
            "apps.dashboard.services.detection.runner.critical_path.detect",
            side_effect=ValueError("想定外のデータ"),
        ):
            result = runner.run_detection([self.project], dry_run=True)

        failed = [skip for skip in result.skips if skip.reason == SkipReason.DETECTOR_FAILED]

        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].kind, "critical_path")

    def test_落ちた検知器は検知なしではなく判定不能として残る(self):
        """黙って「検知なし」にすると、危険が無いのか見られていないのか分からない。"""

        with mock.patch(
            "apps.dashboard.services.detection.runner.defect_rate.detect",
            side_effect=RuntimeError("boom"),
        ):
            result = runner.run_detection([self.project], dry_run=True)

        self.assertTrue(any(skip.reason == SkipReason.DETECTOR_FAILED for skip in result.skips))


class OpsRulesNullTests(TestCase):
    """更新日時が入っていないタスクでも入力標準ルールの画面を落とさない。

    DB は NOT NULL だが、未保存のインスタンスや `only()` で読み落とした
    オブジェクトを渡す経路では None が届く。同じ判定をしている
    `silent_fire._stale_days` は None を守っており、ここだけ無防備だった。
    """

    def test_更新日時が無くても例外にせず違反として返す(self):
        from apps.dashboard.services.ops_rules import _check_stale_update

        task = WbsTask(wbs_code="1.1", name="更新日時が欠けたタスク", owner="担当")
        task.updated_at = None

        message = _check_stale_update(task, cutoff=date(2026, 8, 14))

        self.assertIn("記録されていません", message)


class AlertOrderingTests(TestCase):
    def test_重要度順は辞書順ではなく重要度で並ぶ(self):
        """`severity` は文字列。そのまま並べると warning が info より下へ落ちる。"""

        tenant = Tenant.objects.create(code="acme", name="ACME")
        project = Project.objects.create(tenant=tenant, code="p1", name="案件")
        now = timezone.now()

        for severity in (Alert.Severity.INFO, Alert.Severity.WARNING, Alert.Severity.CRITICAL):
            Alert.objects.create(
                project=project,
                category=Alert.Category.SCHEDULE,
                severity=severity,
                title=f"{severity} のアラート",
                detected_at=now - timedelta(minutes=1),
            )

        overview = build_overview(Project.objects.filter(pk=project.pk))
        severities = [ranked.alert.severity for ranked in overview.ranked_alerts]

        self.assertEqual(
            severities[:3],
            [Alert.Severity.CRITICAL, Alert.Severity.WARNING, Alert.Severity.INFO],
        )
