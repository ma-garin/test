"""計数・目標管理の体験用データ投入。

    python manage.py seed_performance --tenant demo

部1・課2・プロジェクト3 の階層に、期初計画と期中変更計画（第1次）、
上期の実績、KPI の目標と実績を入れる。期中変更は「営業1課だけを見直した」
形にしてあり、行の無い組織が前の版を引き継ぐ挙動を画面で確認できる。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.performance.constants import (
    FigureSource,
    KpiAggregation,
    KpiDirection,
    OrgLevel,
    PlanKind,
    PlanStatus,
)
from apps.performance.models import (
    ActualFigure,
    FiscalYear,
    KpiDefinition,
    KpiResult,
    KpiTarget,
    OrgMember,
    OrgUnit,
    PlanFigure,
    PlanVersion,
)

#: 期初計画の月額（売上, 粗利, 利益）。組織コードごとに置く。
PLAN_MONTHLY = {
    "sec-sales-1": (Decimal("12000000"), Decimal("3600000"), Decimal("1800000")),
    "sec-sales-2": (Decimal("8000000"), Decimal("2400000"), Decimal("1000000")),
    "prj-alpha": (Decimal("5000000"), Decimal("1500000"), Decimal("700000")),
    "prj-beta": (Decimal("4000000"), Decimal("1000000"), Decimal("400000")),
    "prj-gamma": (Decimal("3000000"), Decimal("900000"), Decimal("300000")),
}

#: 期中変更で見直した組織だけを持つ。触っていない組織は期初計画を引き継ぐ。
REVISED_MONTHLY = {
    "sec-sales-1": (Decimal("10000000"), Decimal("2800000"), Decimal("1200000")),
    "prj-alpha": (Decimal("4200000"), Decimal("1200000"), Decimal("500000")),
}

#: 実績の達成度合い。組織ごとに計画比を変え、達成／要注意／未達が並ぶようにする。
ACTUAL_RATIO = {
    "sec-sales-1": Decimal("0.92"),
    "sec-sales-2": Decimal("1.05"),
    "prj-alpha": Decimal("0.78"),
    "prj-beta": Decimal("1.02"),
    "prj-gamma": Decimal("0.88"),
}


class Command(BaseCommand):
    help = "組織・年度・計数計画・実績・KPI の体験用データを投入します。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", default="demo", help="テナントコード")
        parser.add_argument("--year", type=int, default=2026, help="年度の開始年（4月始まり）")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        tenant, _ = Tenant.objects.get_or_create(
            code=options["tenant"], defaults={"name": "体験用テナント"}
        )
        manager = self._manager(tenant)
        fiscal_year = self._fiscal_year(tenant, options["year"])
        units = self._org_units(tenant, manager)
        members = self._members(tenant, units)

        initial = self._plan_version(
            tenant, fiscal_year, PlanKind.INITIAL, 0, fiscal_year.start_on, "期初計画"
        )
        revised = self._plan_version(
            tenant,
            fiscal_year,
            PlanKind.REVISED,
            1,
            date(options["year"], 10, 1),
            "下期見直し（営業1課の受注減）",
        )

        self._plan_figures(initial, units, fiscal_year, PLAN_MONTHLY, members)
        self._plan_figures(revised, units, fiscal_year, REVISED_MONTHLY, members)
        actual_months = self._actual_figures(fiscal_year, units, members)
        self._kpis(tenant, fiscal_year, units, initial, actual_months)

        self.stdout.write(
            self.style.SUCCESS(
                f"計数データを投入しました。組織 {len(units)} 件 / メンバー {len(members)} 件 / "
                f"計画 {PlanFigure.objects.filter(plan_version__fiscal_year=fiscal_year).count()} 行 / "
                f"実績 {ActualFigure.objects.filter(fiscal_year=fiscal_year).count()} 行"
            )
        )

    def _manager(self, tenant: Tenant) -> User:
        user, created = User.objects.get_or_create(
            email="line-manager@example.com",
            defaults={
                "username": "line-manager",
                "display_name": "ラインマネージャー",
                "tenant": tenant,
                "role": Role.PROJECT_MANAGER,
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        return user

    def _fiscal_year(self, tenant: Tenant, start_year: int) -> FiscalYear:
        year, _ = FiscalYear.objects.get_or_create(
            tenant=tenant,
            code=f"FY{start_year}",
            defaults={
                "name": f"{start_year}年度",
                "start_on": date(start_year, 4, 1),
                "end_on": date(start_year + 1, 3, 31),
                "is_current": True,
            },
        )

        return year

    def _org_units(self, tenant: Tenant, manager: User) -> dict:
        definitions = (
            ("div-sales", "第一営業部", OrgLevel.DIVISION, None),
            ("sec-sales-1", "営業1課", OrgLevel.SECTION, "div-sales"),
            ("sec-sales-2", "営業2課", OrgLevel.SECTION, "div-sales"),
            ("prj-alpha", "Alphaプロジェクト", OrgLevel.PROJECT, "sec-sales-1"),
            ("prj-beta", "Betaプロジェクト", OrgLevel.PROJECT, "sec-sales-1"),
            ("prj-gamma", "Gammaプロジェクト", OrgLevel.PROJECT, "sec-sales-2"),
        )
        units: dict = {}

        for code, name, level, parent_code in definitions:
            unit, _ = OrgUnit.objects.update_or_create(
                tenant=tenant,
                code=code,
                defaults={
                    "name": name,
                    "level": level,
                    "parent": units.get(parent_code),
                    # 部長が全体を見る形にする。配下は自動的に参照できる。
                    "manager": manager if level == OrgLevel.DIVISION else None,
                    "deleted_at": None,
                },
            )
            units[code] = unit

        return units

    def _members(self, tenant: Tenant, units: dict) -> dict:
        definitions = (
            ("E0001", "山田 太郎", "sec-sales-1", "課長"),
            ("E0002", "鈴木 花子", "prj-alpha", ""),
            ("E0003", "佐藤 次郎", "prj-beta", ""),
            ("E0004", "田中 三郎", "prj-gamma", ""),
        )
        members: dict = {}

        for code, name, org_code, title in definitions:
            member, _ = OrgMember.objects.update_or_create(
                tenant=tenant,
                employee_code=code,
                defaults={"name": name, "org_unit": units[org_code], "title": title},
            )
            members[code] = member

        return members

    def _plan_version(
        self,
        tenant: Tenant,
        fiscal_year: FiscalYear,
        kind: str,
        revision: int,
        effective_from: date,
        note: str,
    ) -> PlanVersion:
        version, _ = PlanVersion.objects.update_or_create(
            fiscal_year=fiscal_year,
            kind=kind,
            revision=revision,
            defaults={
                "tenant": tenant,
                "effective_from": effective_from,
                "status": PlanStatus.ACTIVE,
                "note": note,
            },
        )

        return version

    def _plan_figures(
        self, version: PlanVersion, units: dict, fiscal_year: FiscalYear, monthly: dict, members: dict
    ) -> None:
        months = [
            month for month in fiscal_year.months if month >= version.effective_from
        ]

        for code, amounts in monthly.items():
            for month in months:
                PlanFigure.objects.update_or_create(
                    plan_version=version,
                    org_unit=units[code],
                    member=None,
                    month=month,
                    defaults={
                        "revenue": amounts[0],
                        "gross_profit": amounts[1],
                        "operating_profit": amounts[2],
                        "source": FigureSource.CSV,
                    },
                )

        # 個人配分は営業1課の課長分だけ入れ、内訳表示を確認できるようにする。
        member = members.get("E0001")

        if member is not None and "sec-sales-1" in monthly:
            amounts = monthly["sec-sales-1"]

            for month in months:
                PlanFigure.objects.update_or_create(
                    plan_version=version,
                    org_unit=units["sec-sales-1"],
                    member=member,
                    month=month,
                    defaults={
                        "revenue": amounts[0] / 2,
                        "gross_profit": amounts[1] / 2,
                        "operating_profit": amounts[2] / 2,
                        "source": FigureSource.CSV,
                    },
                )

    def _actual_figures(self, fiscal_year: FiscalYear, units: dict, members: dict) -> list:
        """上期（6か月）の実績を入れる。下期は未入力のままにして未来月を再現する。"""

        months = fiscal_year.months[:6]

        for code, ratio in ACTUAL_RATIO.items():
            plan = PLAN_MONTHLY[code]

            for month in months:
                ActualFigure.objects.update_or_create(
                    fiscal_year=fiscal_year,
                    org_unit=units[code],
                    member=None,
                    month=month,
                    defaults={
                        "tenant": fiscal_year.tenant,
                        "revenue": (plan[0] * ratio).quantize(Decimal("1")),
                        "gross_profit": (plan[1] * ratio).quantize(Decimal("1")),
                        "operating_profit": (plan[2] * ratio).quantize(Decimal("1")),
                        "source": FigureSource.CSV,
                    },
                )

        member = members["E0001"]
        plan = PLAN_MONTHLY["sec-sales-1"]

        for month in months:
            ActualFigure.objects.update_or_create(
                fiscal_year=fiscal_year,
                org_unit=units["sec-sales-1"],
                member=member,
                month=month,
                defaults={
                    "tenant": fiscal_year.tenant,
                    "revenue": (plan[0] * Decimal("0.45")).quantize(Decimal("1")),
                    "gross_profit": (plan[1] * Decimal("0.45")).quantize(Decimal("1")),
                    "operating_profit": (plan[2] * Decimal("0.45")).quantize(Decimal("1")),
                    "source": FigureSource.CSV,
                },
            )

        return months

    def _kpis(
        self,
        tenant: Tenant,
        fiscal_year: FiscalYear,
        units: dict,
        version: PlanVersion,
        months: list,
    ) -> None:
        definitions = (
            ("kpi-orders", "受注件数", "件", KpiDirection.UP, KpiAggregation.SUM, 24, 5),
            ("kpi-utilization", "稼働率", "%", KpiDirection.UP, KpiAggregation.AVERAGE, 85, 82),
            ("kpi-churn", "解約率", "%", KpiDirection.DOWN, KpiAggregation.AVERAGE, 3, 4),
        )

        for code, name, unit, direction, aggregation, target, monthly_actual in definitions:
            kpi, _ = KpiDefinition.objects.update_or_create(
                tenant=tenant,
                code=code,
                defaults={
                    "name": name,
                    "unit": unit,
                    "direction": direction,
                    "aggregation": aggregation,
                },
            )

            for org_code in ("sec-sales-1", "sec-sales-2"):
                KpiTarget.objects.update_or_create(
                    kpi=kpi,
                    plan_version=version,
                    org_unit=units[org_code],
                    member=None,
                    defaults={"target_value": Decimal(target)},
                )

                for month in months:
                    KpiResult.objects.update_or_create(
                        kpi=kpi,
                        fiscal_year=fiscal_year,
                        org_unit=units[org_code],
                        member=None,
                        month=month,
                        defaults={
                            "tenant": tenant,
                            "actual_value": Decimal(monthly_actual),
                            "source": FigureSource.CSV,
                        },
                    )
