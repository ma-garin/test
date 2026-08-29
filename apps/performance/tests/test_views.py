"""画面の疎通と、手入力の保存・削除・権限。

グリッド入力は「空欄で既存値が消える」ことが仕様なので、画面経由で確かめる。
サービス層のテストだけでは、フォームが空欄を 0 に変換していても気づけない。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.performance.constants import FigureSource, PlanKind, PlanStatus
from apps.performance.models import ActualFigure, PlanFigure, PlanVersion
from apps.performance.services import aggregation
from apps.performance.services import chart as chart_service
from apps.performance.tests import factories


class DashboardTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.manager = factories.make_user(self.tenant, "manager@example.com")
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant, manager=self.manager)
        self.version = factories.make_version(self.tenant, self.year)
        self.month = date(2026, 4, 1)

        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 800)

        self.client.force_login(self.manager)

    def test_dashboard_shows_own_subtree(self) -> None:
        response = self.client.get(
            reverse("performance:dashboard"), {"year": self.year.code, "upto": "2026-04"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"].actual.revenue, 800)
        self.assertEqual(response.context["total"].plan.revenue, 1000)
        self.assertEqual(response.context["total"].revenue_achievement, Decimal("80.00"))

    def test_org_detail_of_other_tenant_is_denied(self) -> None:
        other = factories.make_tenant("t2")
        other_units = factories.make_tree(other)

        response = self.client.get(
            reverse("performance:org_detail", args=[other_units["sec"].pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_org_detail_lists_members_and_monthly_rows(self) -> None:
        member = factories.make_member(self.tenant, self.units["sec"])
        factories.add_actual(self.year, self.units["sec"], self.month, 300, member=member)

        response = self.client.get(
            reverse("performance:org_detail", args=[self.units["sec"].pk]),
            {"year": self.year.code, "upto": "2026-04"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["monthly_rows"]), 1)
        self.assertEqual(response.context["member_summaries"][0].actual.revenue, 300)
        # 個人値は組織合計に足さない。
        self.assertEqual(response.context["summary"].total_actual.revenue, 800)


class FigureEntryTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.manager = factories.make_user(self.tenant, "manager@example.com")
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant, manager=self.manager)
        self.url = reverse("performance:figure_entry")
        self.client.force_login(self.manager)

    def _payload(self, **overrides) -> dict:
        payload = {
            "mode": "actual",
            "org": str(self.units["sec"].pk),
            "m202604_revenue": "1000",
            "m202604_gross_profit": "250",
            "m202604_operating_profit": "100",
        }
        payload.update(overrides)

        return payload

    def test_grid_saves_amounts(self) -> None:
        response = self.client.post(f"{self.url}?year={self.year.code}", self._payload())

        self.assertEqual(response.status_code, 302)
        figure = ActualFigure.objects.get()
        self.assertEqual(figure.revenue, Decimal("1000"))
        self.assertEqual(figure.month, date(2026, 4, 1))
        self.assertEqual(figure.source, FigureSource.MANUAL)
        self.assertEqual(figure.updated_by, self.manager)

    def test_blank_cells_delete_the_existing_row(self) -> None:
        factories.add_actual(self.year, self.units["sec"], date(2026, 4, 1), 500)

        self.client.post(
            f"{self.url}?year={self.year.code}",
            self._payload(
                m202604_revenue="", m202604_gross_profit="", m202604_operating_profit=""
            ),
        )

        self.assertFalse(ActualFigure.objects.exists())

    def test_zero_is_stored_and_not_treated_as_blank(self) -> None:
        self.client.post(
            f"{self.url}?year={self.year.code}",
            self._payload(
                m202604_revenue="0", m202604_gross_profit="0", m202604_operating_profit="0"
            ),
        )

        self.assertEqual(ActualFigure.objects.get().revenue, Decimal("0"))

    def test_gross_profit_cannot_exceed_revenue(self) -> None:
        response = self.client.post(
            f"{self.url}?year={self.year.code}",
            self._payload(m202604_revenue="100", m202604_gross_profit="200"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ActualFigure.objects.exists())
        self.assertIn("m202604_gross_profit", response.context["form"].errors)

    def test_entry_for_invisible_org_is_not_found(self) -> None:
        """見えない組織は「存在しない」。権限の有無を ID の総当たりで探れない。"""

        other_manager = factories.make_user(self.tenant, "other@example.com")
        self.client.force_login(other_manager)

        response = self.client.post(f"{self.url}?year={self.year.code}", self._payload())

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ActualFigure.objects.exists())

    def test_entry_for_visible_but_unmanaged_org_is_denied(self) -> None:
        """所属メンバーは自組織を見られるが、編集はできない。"""

        viewer = factories.make_user(self.tenant, "viewer@example.com", role=Role.VIEWER)
        factories.make_member(self.tenant, self.units["sec"], code="E9", user=viewer)
        self.client.force_login(viewer)

        response = self.client.post(f"{self.url}?year={self.year.code}", self._payload())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ActualFigure.objects.exists())

    def test_plan_mode_writes_to_the_selected_version(self) -> None:
        version = factories.make_version(self.tenant, self.year)

        self.client.post(
            f"{self.url}?year={self.year.code}",
            self._payload(mode="plan", plan=str(version.pk)),
        )

        figure = PlanFigure.objects.get()
        self.assertEqual(figure.plan_version, version)
        self.assertEqual(figure.revenue, Decimal("1000"))


class PlanViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.manager = factories.make_user(self.tenant, "manager@example.com")
        self.year = factories.make_year(self.tenant)
        factories.make_tree(self.tenant, manager=self.manager)
        self.client.force_login(self.manager)

    def test_creating_a_revision_when_initial_exists(self) -> None:
        factories.make_version(self.tenant, self.year)

        response = self.client.post(
            f"{reverse('performance:plan_create')}?year={self.year.code}",
            {
                "kind": PlanKind.REVISED,
                "name": "下期見直し",
                "effective_from": "2026-10-15",
                "status": PlanStatus.DRAFT,
                "note": "受注減",
            },
        )

        self.assertEqual(response.status_code, 302)
        revised = PlanVersion.objects.get(kind=PlanKind.REVISED)
        self.assertEqual(revised.revision, 1)
        # 適用は月単位。日付は月初へ丸める。
        self.assertEqual(revised.effective_from, date(2026, 10, 1))

    def test_effective_month_must_be_inside_the_year(self) -> None:
        factories.make_version(self.tenant, self.year)

        response = self.client.post(
            f"{reverse('performance:plan_create')}?year={self.year.code}",
            {"kind": PlanKind.REVISED, "effective_from": "2028-04-01", "status": PlanStatus.DRAFT},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlanVersion.objects.filter(kind=PlanKind.REVISED).exists())

    def test_activation_puts_the_version_into_aggregation(self) -> None:
        version = factories.make_version(self.tenant, self.year, status=PlanStatus.DRAFT)

        response = self.client.post(reverse("performance:plan_activate", args=[version.pk]))

        version.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(version.status, PlanStatus.ACTIVE)


class MasterViewTests(TestCase):
    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.admin = factories.make_user(
            self.tenant, "admin@example.com", role=Role.TENANT_ADMIN
        )
        self.client.force_login(self.admin)

    def test_org_creation_rejects_level_gap(self) -> None:
        division = factories.make_org(self.tenant, "div", "division")

        response = self.client.post(
            reverse("performance:org_create"),
            {
                "code": "prj",
                "name": "飛び級プロジェクト",
                "level": "project",
                "parent": str(division.pk),
                "sort_order": "100",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)

    def test_line_manager_cannot_reach_master_screens(self) -> None:
        manager = factories.make_user(self.tenant, "manager@example.com")
        self.client.force_login(manager)

        response = self.client.get(reverse("performance:org_create"))

        self.assertEqual(response.status_code, 403)


class ImportPermissionTests(TestCase):
    """CSV が権限の抜け道にならないこと。"""

    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.manager = factories.make_user(self.tenant, "manager@example.com")
        self.units = factories.make_tree(self.tenant, manager=self.manager)
        self.year = factories.make_year(self.tenant)
        self.url = reverse("performance:import")
        self.client.force_login(self.manager)

    def _upload(self, name: str, body: str):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, body.encode("utf-8"), content_type="text/csv")

    def test_line_manager_can_import_figures(self) -> None:
        response = self.client.post(
            self.url,
            {
                "kind": "actual_figure",
                "fiscal_year": str(self.year.pk),
                "csv_file": self._upload(
                    "a.csv",
                    "org_code,employee_code,month,revenue,gross_profit,operating_profit,note\n"
                    "sec,,2026-04,1000,250,100,\n",
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ActualFigure.objects.count(), 1)

    def test_line_manager_cannot_import_org_master(self) -> None:
        response = self.client.post(
            self.url,
            {
                "kind": "org_unit",
                "csv_file": self._upload(
                    "o.csv",
                    "code,name,level,parent_code,manager_email,sort_order\n"
                    "hijack,乗っ取り部,division,,,10\n",
                ),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            self.units["div"].__class__.objects.filter(code="hijack").exists()
        )


class GridDisplayTests(TestCase):
    """入力欄の初期表示。円単位の金額に `.00` を出さない。"""

    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.manager = factories.make_user(self.tenant, "manager@example.com")
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant, manager=self.manager)
        self.client.force_login(self.manager)

    def test_integer_amounts_render_without_decimals(self) -> None:
        factories.add_actual(self.year, self.units["sec"], date(2026, 4, 1), 11040000)

        response = self.client.get(
            reverse("performance:figure_entry"),
            {"year": self.year.code, "org": str(self.units["sec"].pk), "mode": "actual"},
        )

        self.assertEqual(
            response.context["form"]["m202604_revenue"].initial, Decimal("11040000")
        )
        self.assertContains(response, 'value="11040000"')


class DashboardSimplificationTests(TestCase):
    """1画面1指標で、手当が要るものだけが前に出ること。"""

    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.manager = factories.make_user(self.tenant, "manager@example.com")
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant, manager=self.manager)
        self.version = factories.make_version(self.tenant, self.year)
        self.month = date(2026, 4, 1)
        self.client.force_login(self.manager)

    def _get(self, **params):
        params.setdefault("year", self.year.code)
        params.setdefault("upto", "2026-04")

        return self.client.get(reverse("performance:dashboard"), params)

    def test_summary_carries_every_figure_and_its_plan(self) -> None:
        """サマリ表に売上・粗利・利益と率が、計画・実績・差・対計画比つきで並ぶ。"""

        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 800)

        rows = {row.label: row for row in self._get().context["summary_rows"]}

        self.assertEqual(
            list(rows), ["売上", "粗利", "粗利率", "利益", "利益率"]
        )
        self.assertEqual(rows["売上"].plan, Decimal("1000"))
        self.assertEqual(rows["売上"].actual, Decimal("800"))
        self.assertEqual(rows["売上"].diff, Decimal("-200"))
        self.assertEqual(rows["売上"].achievement, Decimal("80.00"))
        self.assertEqual(rows["利益"].actual, Decimal("80"))

    def test_rate_rows_report_point_difference_not_achievement(self) -> None:
        """率は差（ポイント）で見る。率どうしの比は意味を持たない。"""

        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 800)

        rows = {row.label: row for row in self._get().context["summary_rows"]}

        self.assertIsNone(rows["利益率"].achievement)
        self.assertEqual(rows["利益率"].actual, Decimal("10.00"))

    def test_org_line_shows_all_three_metrics(self) -> None:
        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 800)

        line = self._get().context["lines"][0]

        self.assertEqual(line.revenue.actual, Decimal("800"))
        self.assertEqual(line.gross_profit.actual, Decimal("200"))
        self.assertEqual(line.operating_profit.actual, Decimal("80"))
        self.assertEqual(line.revenue.achievement, Decimal("80.00"))

    def test_metric_switches_the_chart_series(self) -> None:
        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 800)

        revenue = self._get().context["chart"]
        profit = self._get(metric="operating_profit").context["chart"]

        self.assertEqual(revenue.bars[0].actual, Decimal("800"))
        self.assertEqual(profit.bars[0].actual, Decimal("80"))
        # グラフは年度12か月ぶんを描く。累計期間だけだと落ちた月が見えない。
        self.assertEqual(len(revenue.bars), 12)

    def test_unknown_metric_falls_back_to_revenue(self) -> None:
        self.assertEqual(self._get(metric="bogus").context["metric"], "revenue")

    def test_attention_list_holds_behind_and_near_miss_rows(self) -> None:
        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 910)

        lines = self._get().context["attention_lines"]

        self.assertEqual([line.label for line in lines], ["sec"])
        self.assertEqual(lines[0].status_label, "あと少し")

    def test_nothing_to_watch_when_every_org_meets_plan(self) -> None:
        factories.add_plan(self.version, self.units["sec"], self.month, 1000)
        factories.add_actual(self.year, self.units["sec"], self.month, 1100)

        response = self._get()

        self.assertEqual(response.context["attention_lines"], [])
        self.assertContains(response, "未達の組織・KPI はありません")


class ChartScaleTests(TestCase):
    """グラフの目盛りが、実測値のすぐ上で止まること。

    刻みが粗すぎると棒が図の下半分にへばりつき、月ごとの差が読めなくなる。
    """

    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant)
        self.version = factories.make_version(self.tenant, self.year)

    def _chart(self, revenue):
        factories.add_actual(self.year, self.units["sec"], date(2026, 4, 1), revenue)
        report = aggregation.build_report(
            self.year, list(self.units.values()), self.year.months
        )

        return chart_service.monthly_chart(report.monthly_rows(self.units["sec"]), "revenue")

    def test_axis_top_sits_just_above_the_data(self) -> None:
        chart = self._chart(32_000_000)

        # 上限は 4,000万。5,000万まで取ると棒が図の6割で止まる。
        self.assertEqual(chart.ticks[-1]["label"], "40")

    def test_bar_reaches_most_of_the_plot(self) -> None:
        chart = self._chart(32_000_000)
        tallest = max(bar.height for bar in chart.bars)
        plot_height = chart.baseline - 16

        self.assertGreater(tallest / plot_height, 0.7)

    def test_empty_year_is_reported_as_no_data(self) -> None:
        report = aggregation.build_report(
            self.year, list(self.units.values()), self.year.months
        )
        chart = chart_service.monthly_chart(report.monthly_rows(self.units["sec"]), "revenue")

        self.assertFalse(chart.has_data)


class ChartTickTests(TestCase):
    """目盛りの数字が、実際の目盛り位置と食い違わないこと。"""

    def setUp(self) -> None:
        self.tenant = factories.make_tenant()
        self.year = factories.make_year(self.tenant)
        self.units = factories.make_tree(self.tenant)

    def test_half_tick_keeps_its_fraction(self) -> None:
        factories.add_actual(self.year, self.units["sec"], date(2026, 4, 1), 21_000_000)
        report = aggregation.build_report(self.year, list(self.units.values()), self.year.months)
        chart = chart_service.monthly_chart(report.monthly_rows(self.units["sec"]), "revenue")

        # 上限 2,500万 → 中間の目盛りは 1,250万。「12」と丸めない。
        self.assertEqual([tick["label"] for tick in chart.ticks], ["0", "12.5", "25"])
