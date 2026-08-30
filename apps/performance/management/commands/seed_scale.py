"""実運用の規模で計数データを投入する。

    python manage.py seed_scale --tenant scale

部6・課30・プロジェクト150・要員650。体験用の `seed_performance` は
部1・課2・プロジェクト3 で、実運用の約 1/50 しかない。画面が1画面に
収まるか、集計が現実的な時間で返るかは、この規模でしか分からない。

数字は決定的に作る（乱数の種を固定する）。実行のたびに違う結果が出ると、
「直したのか、たまたま良い数字が出たのか」が区別できない。
"""

from __future__ import annotations

import random
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

DIVISIONS = 6
SECTIONS = 30
PROJECTS = 150
MEMBERS = 650

#: 実績が入っている月数。期の途中を再現する。
ACTUAL_MONTHS = 6

SEED = 20260831

DIVISION_NAMES = (
    "第一検証部",
    "第二検証部",
    "第三検証部",
    "モビリティ検証部",
    "金融システム検証部",
    "ヘルスケア検証部",
)

DOMAINS = (
    "車載ECU",
    "決済アプリ",
    "医療機器",
    "基幹システム",
    "スマート家電",
    "生産管理",
    "保険基幹",
    "物流基盤",
    "通信制御",
    "会計システム",
)

PHASES = ("結合検証", "受入検証", "適合性検証", "性能検証", "回帰検証")

SURNAMES = (
    "山田", "鈴木", "佐藤", "田中", "高橋", "伊藤", "渡辺", "中村",
    "小林", "加藤", "吉田", "山本", "松本", "井上", "木村", "林",
)

GIVEN_NAMES = (
    "太郎", "花子", "次郎", "三郎", "美咲", "健一", "真理", "大輔",
    "彩", "翔太", "由美", "拓也", "恵子", "涼", "直樹", "千尋",
)

TITLES = ("テストリーダー", "テスト設計", "テスト実施", "自動化エンジニア", "課長")


class Command(BaseCommand):
    help = "実運用規模（部6・課30・プロジェクト150・要員650）の計数データを投入する"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", default="scale", help="テナントコード")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        # 種にテナントコードを混ぜる。全テナントが同じ数字だと、
        # 他社の数字が混ざっていても画面から気づけない。
        tenant = self._tenant(options["tenant"])
        rng = random.Random(f"{SEED}-{tenant.code}")
        manager = self._manager(tenant)

        fiscal_year = self._year(tenant, 2026, is_current=True)
        prior_year = self._year(tenant, 2025, is_current=False)

        units = self._orgs(tenant, manager, rng)
        members = self._members(tenant, units, rng)

        initial = self._version(tenant, fiscal_year, PlanKind.INITIAL, 0, fiscal_year.start_on)
        revised = self._version(tenant, fiscal_year, PlanKind.REVISED, 1, date(2026, 10, 1))
        self._year_version(tenant, prior_year)

        leaves = [unit for unit in units if unit.level == OrgLevel.PROJECT]
        plans = self._plan_amounts(leaves, rng)

        self._plan_figures(initial, plans, fiscal_year.months, members, rng)
        # 期中変更は一部の組織だけを見直す。触っていない組織は前の版を引き継ぐ。
        revised_targets = {
            unit.pk: amounts
            for unit, amounts in plans.items()
            if rng.random() < 0.3
        }
        self._plan_figures(
            revised,
            {unit: revised_targets[unit.pk] for unit in plans if unit.pk in revised_targets},
            [m for m in fiscal_year.months if m >= revised.effective_from],
            members,
            rng,
            scale=Decimal("0.9"),
        )

        ratios = {unit.pk: Decimal(str(round(rng.uniform(0.72, 1.12), 3))) for unit in leaves}
        self._actuals(fiscal_year, plans, ratios, fiscal_year.months[:ACTUAL_MONTHS], members, rng)
        self._actuals(
            prior_year,
            plans,
            {pk: ratio * Decimal("0.85") for pk, ratio in ratios.items()},
            prior_year.months[:ACTUAL_MONTHS],
            members,
            rng,
        )

        self._kpis(tenant, fiscal_year, [initial, revised], units, rng)

        self.stdout.write(
            self.style.SUCCESS(
                f"投入しました。組織 {len(units)} 件 / 要員 {len(members)} 名 / "
                f"計画 {PlanFigure.objects.filter(plan_version__fiscal_year=fiscal_year).count()} 行 / "
                f"実績 {ActualFigure.objects.filter(fiscal_year=fiscal_year).count()} 行 / "
                f"KPI実績 {KpiResult.objects.filter(fiscal_year=fiscal_year).count()} 行"
            )
        )

    def _tenant(self, code: str) -> Tenant:
        tenant, _ = Tenant.objects.update_or_create(
            code=code, defaults={"name": "実規模テナント"}
        )

        return tenant

    def _manager(self, tenant: Tenant) -> User:
        # 利用者はテナントごとに作る。固定のアドレスにすると、2つめの
        # テナントを入れたときに見る人がいない状態になり、
        # テナント間で数字が混ざらないことを画面で確かめられない。
        email = f"{tenant.code}-manager@example.com"
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": f"{tenant.code}-manager",
                "tenant": tenant,
                "role": Role.PROJECT_MANAGER,
                "display_name": f"{tenant.name} ラインマネージャー",
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        return user

    def _year(self, tenant: Tenant, start_year: int, is_current: bool) -> FiscalYear:
        year, _ = FiscalYear.objects.update_or_create(
            tenant=tenant,
            code=f"FY{start_year}",
            defaults={
                "name": f"{start_year}年度",
                "start_on": date(start_year, 4, 1),
                "end_on": date(start_year + 1, 3, 31),
                "is_current": is_current,
            },
        )

        return year

    def _orgs(self, tenant: Tenant, manager: User, rng: random.Random) -> list[OrgUnit]:
        units: list[OrgUnit] = []
        divisions: list[OrgUnit] = []

        for index in range(DIVISIONS):
            unit, _ = OrgUnit.objects.update_or_create(
                tenant=tenant,
                code=f"div-{index + 1:02d}",
                defaults={
                    "name": DIVISION_NAMES[index],
                    "level": OrgLevel.DIVISION,
                    "parent": None,
                    "manager": manager,
                    "sort_order": index,
                },
            )
            divisions.append(unit)
            units.append(unit)

        sections: list[OrgUnit] = []

        for index in range(SECTIONS):
            parent = divisions[index % DIVISIONS]
            unit, _ = OrgUnit.objects.update_or_create(
                tenant=tenant,
                code=f"sec-{index + 1:03d}",
                defaults={
                    "name": f"{parent.name} 第{index // DIVISIONS + 1}課",
                    "level": OrgLevel.SECTION,
                    "parent": parent,
                    "sort_order": index,
                },
            )
            sections.append(unit)
            units.append(unit)

        for index in range(PROJECTS):
            parent = sections[index % SECTIONS]
            domain = DOMAINS[index % len(DOMAINS)]
            phase = PHASES[(index // len(DOMAINS)) % len(PHASES)]
            unit, _ = OrgUnit.objects.update_or_create(
                tenant=tenant,
                code=f"prj-{index + 1:03d}",
                defaults={
                    "name": f"{domain} {phase} #{index + 1:03d}",
                    "level": OrgLevel.PROJECT,
                    "parent": parent,
                    "sort_order": index,
                },
            )
            units.append(unit)

        return units

    def _members(self, tenant: Tenant, units: list[OrgUnit], rng: random.Random) -> list[OrgMember]:
        projects = [unit for unit in units if unit.level == OrgLevel.PROJECT]
        members = []

        for index in range(MEMBERS):
            org = projects[index % len(projects)]
            surname = SURNAMES[index % len(SURNAMES)]
            given = GIVEN_NAMES[(index // len(SURNAMES)) % len(GIVEN_NAMES)]
            member, _ = OrgMember.objects.update_or_create(
                tenant=tenant,
                employee_code=f"E{index + 1:04d}",
                defaults={
                    "name": f"{surname} {given}{index + 1:04d}",
                    "org_unit": org,
                    "title": TITLES[index % len(TITLES)],
                },
            )
            members.append(member)

        return members

    def _version(self, tenant, fiscal_year, kind, revision, effective_from) -> PlanVersion:
        version, _ = PlanVersion.objects.update_or_create(
            fiscal_year=fiscal_year,
            kind=kind,
            revision=revision,
            defaults={
                "tenant": tenant,
                "effective_from": effective_from,
                "status": PlanStatus.ACTIVE,
                "note": "実規模の投入データ",
            },
        )

        return version

    def _year_version(self, tenant, prior_year) -> PlanVersion:
        return self._version(tenant, prior_year, PlanKind.INITIAL, 0, prior_year.start_on)

    def _plan_amounts(self, leaves, rng) -> dict:
        """プロジェクトごとの月額。計数はここ（末端）だけが持つ。"""

        amounts = {}

        for unit in leaves:
            revenue = Decimal(rng.randrange(2_000_000, 9_000_000, 100_000))
            gross = (revenue * Decimal(str(round(rng.uniform(0.24, 0.34), 3)))).quantize(Decimal("1"))
            profit = (gross * Decimal(str(round(rng.uniform(0.32, 0.48), 3)))).quantize(Decimal("1"))
            amounts[unit] = (revenue, gross, profit)

        return amounts

    def _plan_figures(self, version, amounts, months, members, rng, scale=Decimal("1")) -> None:
        by_org: dict = {}

        for member in members:
            by_org.setdefault(member.org_unit_id, []).append(member)

        rows = []

        for unit, (revenue, gross, profit) in amounts.items():
            values = (
                (revenue * scale).quantize(Decimal("1")),
                (gross * scale).quantize(Decimal("1")),
                (profit * scale).quantize(Decimal("1")),
            )

            for month in months:
                rows.append(
                    PlanFigure(
                        plan_version=version,
                        org_unit=unit,
                        member=None,
                        month=month,
                        revenue=values[0],
                        gross_profit=values[1],
                        operating_profit=values[2],
                        source=FigureSource.CSV,
                    )
                )

                # 個人配分は組織の金額の内訳。全量ではないので合計は組織を超えない。
                team = by_org.get(unit.pk, [])
                share = Decimal("0.8") / len(team) if team else Decimal("0")

                for member in team:
                    rows.append(
                        PlanFigure(
                            plan_version=version,
                            org_unit=unit,
                            member=member,
                            month=month,
                            revenue=(values[0] * share).quantize(Decimal("1")),
                            gross_profit=(values[1] * share).quantize(Decimal("1")),
                            operating_profit=(values[2] * share).quantize(Decimal("1")),
                            source=FigureSource.CSV,
                        )
                    )

        PlanFigure.objects.filter(plan_version=version).delete()
        PlanFigure.objects.bulk_create(rows, batch_size=2000)

    def _actuals(self, fiscal_year, amounts, ratios, months, members, rng) -> None:
        by_org: dict = {}

        for member in members:
            by_org.setdefault(member.org_unit_id, []).append(member)

        rows = []

        for unit, (revenue, gross, profit) in amounts.items():
            ratio = ratios[unit.pk]

            for month in months:
                values = (
                    (revenue * ratio).quantize(Decimal("1")),
                    (gross * ratio).quantize(Decimal("1")),
                    (profit * ratio).quantize(Decimal("1")),
                )
                rows.append(
                    ActualFigure(
                        tenant=fiscal_year.tenant,
                        fiscal_year=fiscal_year,
                        org_unit=unit,
                        member=None,
                        month=month,
                        revenue=values[0],
                        gross_profit=values[1],
                        operating_profit=values[2],
                        source=FigureSource.CSV,
                    )
                )

                team = by_org.get(unit.pk, [])
                share = Decimal("0.8") / len(team) if team else Decimal("0")

                for member in team:
                    rows.append(
                        ActualFigure(
                            tenant=fiscal_year.tenant,
                            fiscal_year=fiscal_year,
                            org_unit=unit,
                            member=member,
                            month=month,
                            revenue=(values[0] * share).quantize(Decimal("1")),
                            gross_profit=(values[1] * share).quantize(Decimal("1")),
                            operating_profit=(values[2] * share).quantize(Decimal("1")),
                            source=FigureSource.CSV,
                        )
                    )

        ActualFigure.objects.filter(fiscal_year=fiscal_year).delete()
        ActualFigure.objects.bulk_create(rows, batch_size=2000)

    def _kpis(self, tenant, fiscal_year, versions, units, rng) -> None:
        definitions = []

        for code, name, unit_label, direction, aggregation in (
            ("kpi-churn", "解約率", "%", KpiDirection.DOWN, KpiAggregation.AVERAGE),
            ("kpi-utilization", "稼働率", "%", KpiDirection.UP, KpiAggregation.AVERAGE),
            ("kpi-orders", "受注件数", "件", KpiDirection.UP, KpiAggregation.SUM),
        ):
            definition, _ = KpiDefinition.objects.update_or_create(
                tenant=tenant,
                code=code,
                defaults={
                    "name": name,
                    "unit": unit_label,
                    "direction": direction,
                    "aggregation": aggregation,
                    "is_active": True,
                },
            )
            definitions.append(definition)

        # KPI は課ごとに置く。プロジェクト単位まで持たせると、
        # 実運用より細かいデータで画面を評価することになる。
        sections = [unit for unit in units if unit.level == OrgLevel.SECTION]
        targets = []
        results = []

        for definition in definitions:
            base = {"解約率": Decimal("3"), "稼働率": Decimal("85"), "受注件数": Decimal("24")}[
                definition.name
            ]

            for unit in sections:
                # 目標はどの版でも引けるようにする。期中変更版だけに置くと、
                # 上期（期初計画が効いている期間）の判定が全件「未計測」になる。
                for version in versions:
                    targets.append(
                        KpiTarget(
                            kpi=definition,
                            plan_version=version,
                            org_unit=unit,
                            member=None,
                            target_value=base,
                        )
                    )

                for month in fiscal_year.months[:ACTUAL_MONTHS]:
                    drift = Decimal(str(round(rng.uniform(-0.25, 0.25), 3)))
                    results.append(
                        KpiResult(
                            tenant=tenant,
                            kpi=definition,
                            fiscal_year=fiscal_year,
                            org_unit=unit,
                            member=None,
                            month=month,
                            actual_value=(base * (1 + drift)).quantize(Decimal("0.01")),
                            source=FigureSource.CSV,
                        )
                    )

        KpiTarget.objects.filter(plan_version__in=versions).delete()
        KpiResult.objects.filter(fiscal_year=fiscal_year).delete()
        KpiTarget.objects.bulk_create(targets, batch_size=2000)
        KpiResult.objects.bulk_create(results, batch_size=2000)
