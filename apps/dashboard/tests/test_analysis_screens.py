"""分析系4画面（ガント・品質・リスク・KPI・PoC）の表示テスト。

守りたいのは「見て終わる画面にしない」こと。数値が出ているだけでは、
次に何をすればよいかが決まらない。そのため各画面で次の3点を確認する。

1. 手当てが要るもの（不合格・未達・未計測・判定不能）が要約として先に出る
2. そこから台帳へ進める。進めないときは偽リンクではなく「確認先なし」と書く
3. 未計測・目標未設定を「未達」と混同しない
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import KpiMeasurement
from apps.dashboard.views import (
    NO_LEDGER_LABEL,
    _improvement_label,
    _kpi_link,
    _poc_unknown_row,
    _quality_metric_label,
    _quality_next_data,
    _risks_without_due,
)
from apps.projects.models import Project, QualityMetric, Risk


class AnalysisScreenTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="analysis-user",
            email="analysis-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.client.force_login(self.user)

    def html(self, url: str) -> str:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url} が 200 で返っていない")

        return response.content.decode("utf-8")


class GanttScreenTests(AnalysisScreenTestBase):
    """UXP-03: 図の読み方と、図から一覧へ戻る導線。"""

    @property
    def url(self) -> str:
        return f"{reverse('dashboard:tasks')}?view=gantt"

    def test_legend_explains_today_overdue_and_undated(self) -> None:
        html = self.html(self.url)

        self.assertIn("この図の読み方（凡例）", html)
        self.assertIn("今日の基準線", html)
        self.assertIn("期限超過", html)
        self.assertIn("期限未設定", html)

    def test_counts_link_back_to_task_list(self) -> None:
        html = self.html(self.url)
        task_list = reverse("dashboard:tasks")

        self.assertIn("期限超過のタスク一覧へ戻る", html)
        self.assertIn("ブロック中のタスク一覧へ戻る", html)
        self.assertIn(f"{task_list}?due=overdue", html)
        self.assertIn(f"{task_list}?status=blocked", html)

    def test_undated_tasks_have_their_own_list(self) -> None:
        html = self.html(self.url)

        self.assertIn("未計画リスト（期間未設定のタスク）", html)


class QualityScreenTests(AnalysisScreenTestBase):
    """UXP-06: 不合格と未計測を分け、行き先と次のデータを示す。"""

    def create_metric(self, **kwargs) -> QualityMetric:
        values = {
            "project": self.project,
            "measured_on": date(2026, 1, 15),
            "metric_key": "test_pass_rate",
            "metric_label": "",
            "value": Decimal("50"),
            "target_value": Decimal("95"),
            "threshold": Decimal("90"),
            "higher_is_better": True,
        }
        values.update(kwargs)

        return QualityMetric.objects.create(**values)

    def test_failed_metric_is_summarized_with_ledger_links(self) -> None:
        self.create_metric()
        html = self.html(reverse("dashboard:quality"))

        self.assertIn("先に見るもの（不合格・未計測）", html)
        self.assertIn("ゲート未達", html)
        self.assertIn("未対応の不具合一覧を見る", html)
        self.assertIn("文書台帳を見る", html)
        self.assertIn(reverse("projects:defect_list"), html)

    def test_unmeasured_metric_shows_next_data_to_collect(self) -> None:
        self.create_metric(threshold=None, target_value=None, metric_key="rework_rate")
        html = self.html(reverse("dashboard:quality"))

        self.assertIn("未計測", html)
        self.assertIn("次に入力／取得すべきデータ", html)
        self.assertIn("品質ゲートの閾値と目標値を登録してください", html)

    def test_metric_key_is_shown_as_business_term(self) -> None:
        self.create_metric()
        html = self.html(reverse("dashboard:quality"))

        self.assertIn("テスト合格率", html)

    def test_metric_label_wins_over_key_translation(self) -> None:
        metric = SimpleNamespace(metric_label="受入テスト合格率", metric_key="test_pass_rate")

        self.assertEqual(_quality_metric_label(metric), "受入テスト合格率")

    def test_next_data_depends_on_what_is_missing(self) -> None:
        no_threshold = SimpleNamespace(threshold=None, target_value=Decimal("95"))
        measured = SimpleNamespace(threshold=Decimal("90"), target_value=Decimal("95"))

        self.assertIn("閾値を登録してください", _quality_next_data(no_threshold))
        self.assertIn("最新の計測値", _quality_next_data(measured))


class RiskScreenTests(AnalysisScreenTestBase):
    """UXP-09: クイックビュー・計算式・課題化の説明・期限なしの警告。"""

    def create_risk(self, **kwargs) -> Risk:
        values = {
            "project": self.project,
            "title": "外部連携の仕様が未確定",
            "impact": 5,
            "probability": 5,
            "mitigation": "",
            "due_date": None,
            "status": Risk.Status.IDENTIFIED,
        }
        values.update(kwargs)

        return Risk.objects.create(**values)

    def test_quick_views_use_existing_status_filter(self) -> None:
        html = self.html(reverse("dashboard:risk"))

        self.assertIn("顕在化", html)
        self.assertIn(f"{reverse('dashboard:risk')}?status={Risk.Status.MATERIALIZED.value}", html)
        self.assertIn("対策なし（この画面内の一覧へ）", html)

    def test_score_formula_is_shown_near_the_list(self) -> None:
        html = self.html(reverse("dashboard:risk"))

        self.assertIn("スコア = 影響 × 確率", html)

    def test_promote_link_explains_what_is_carried_over(self) -> None:
        html = self.html(reverse("dashboard:risk"))

        self.assertIn("課題として引き継がれ", html)
        self.assertIn("元のリスクはこの一覧に残ります", html)

    def test_high_score_risk_without_due_date_is_warned(self) -> None:
        self.create_risk()
        html = self.html(reverse("dashboard:risk"))

        self.assertIn("期限なしの高スコアリスク", html)

    def test_risks_without_due_only_picks_high_score(self) -> None:
        high = SimpleNamespace(tone="r", risk=SimpleNamespace(due_date=None))
        low = SimpleNamespace(tone="g", risk=SimpleNamespace(due_date=None))
        dated = SimpleNamespace(tone="r", risk=SimpleNamespace(due_date=date(2026, 3, 1)))

        self.assertEqual(_risks_without_due((high, low, dated)), (high,))


class KpiScreenTests(AnalysisScreenTestBase):
    """UXP-12: 未達・未計測・目標未設定を分け、行き先が無ければそう書く。"""

    def create_measurement(self, **kwargs) -> KpiMeasurement:
        values = {
            "project": self.project,
            "kind": KpiMeasurement.Kind.REPORT_HOURS,
            "measured_on": date(2026, 1, 20),
            "baseline_value": Decimal("20"),
            "actual_value": Decimal("15"),
            "target_value": Decimal("10"),
            "unit": "時間",
        }
        values.update(kwargs)

        return KpiMeasurement.objects.create(**values)

    def test_unachieved_and_missing_are_separated(self) -> None:
        self.create_measurement()
        html = self.html(reverse("dashboard:kpi"))

        self.assertIn("先に見るもの（未達・未計測）", html)
        self.assertIn("未達", html)
        self.assertIn("未計測", html)
        self.assertIn("事実誤認件数", html)

    def test_missing_destination_is_written_not_faked(self) -> None:
        self.create_measurement()
        html = self.html(reverse("dashboard:kpi"))

        self.assertIn(NO_LEDGER_LABEL, html)
        self.assertEqual(_kpi_link(KpiMeasurement.Kind.REPORT_HOURS.value), ("", NO_LEDGER_LABEL))

    def test_improvement_shows_direction_and_unit(self) -> None:
        self.create_measurement()
        html = self.html(reverse("dashboard:kpi"))

        self.assertIn("低いほど良い", html)
        self.assertIn("時間", html)
        self.assertEqual(_improvement_label(12), "良い方向")
        self.assertEqual(_improvement_label(-5), "悪い方向")
        self.assertEqual(_improvement_label(None), "算出不能")

    def test_target_missing_is_not_counted_as_unachieved(self) -> None:
        self.create_measurement(target_value=None)
        html = self.html(reverse("dashboard:kpi"))

        self.assertIn("目標未設定", html)
        self.assertIn("目標値は KPI 計測データ", html)


class PocScreenTests(AnalysisScreenTestBase):
    """UXP-13: 不合格・判定不能の抜き出しと、実演手順の折りたたみ。"""

    def test_summary_lists_unknown_with_required_data_and_source(self) -> None:
        html = self.html(reverse("dashboard:poc"))

        self.assertIn("手当てが要る条件（不合格・判定不能）", html)
        self.assertIn("必要なデータ", html)
        self.assertIn("取得先", html)

    def test_unknown_row_without_known_key_says_no_destination(self) -> None:
        row = _poc_unknown_row(SimpleNamespace(key="unheard_of_key"))

        self.assertEqual(row.url, "")
        self.assertEqual(row.link_label, NO_LEDGER_LABEL)

    def test_report_hours_unknown_row_points_at_kpi_screen(self) -> None:
        row = _poc_unknown_row(SimpleNamespace(key="report_hours"))

        self.assertEqual(row.url, reverse("dashboard:kpi"))
        self.assertIn("作業時間", row.data_need)

    def test_demo_steps_are_collapsed_in_details(self) -> None:
        template = Path(settings.BASE_DIR) / "templates/pages/poc_evaluation.html"
        source = template.read_text(encoding="utf-8")

        self.assertIn("<details>", source)
        self.assertLess(source.index("<details>"), source.index("1. 下表の成果物"))
