"""テスト用のデータ組み立て。

各テストが同じ組織ツリー（部1・課1・PJ1）を使い、階層の積み上げと
参照範囲の検証を同じ土台で書けるようにする。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.performance.constants import FigureSource, OrgLevel, PlanKind, PlanStatus
from apps.performance.models import (
    ActualFigure,
    FiscalYear,
    OrgMember,
    OrgUnit,
    PlanFigure,
    PlanVersion,
)


def make_tenant(code: str = "t1", name: str = "テナント") -> Tenant:
    return Tenant.objects.create(code=code, name=name)


def make_user(tenant: Tenant, email: str, role: str = Role.PROJECT_MANAGER, **kwargs) -> User:
    return User.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password="x",
        tenant=tenant,
        role=role,
        **kwargs,
    )


def make_year(tenant: Tenant, start_year: int = 2026, code: str = "") -> FiscalYear:
    return FiscalYear.objects.create(
        tenant=tenant,
        code=code or f"FY{start_year}",
        name=f"{start_year}年度",
        start_on=date(start_year, 4, 1),
        end_on=date(start_year + 1, 3, 31),
        is_current=True,
    )


def make_org(tenant: Tenant, code: str, level: str, parent=None, manager=None) -> OrgUnit:
    return OrgUnit.objects.create(
        tenant=tenant, code=code, name=code, level=level, parent=parent, manager=manager
    )


def make_tree(tenant: Tenant, manager=None) -> dict:
    """部 → 課 → プロジェクト の3階層を作る。"""

    division = make_org(tenant, "div", OrgLevel.DIVISION, manager=manager)
    section = make_org(tenant, "sec", OrgLevel.SECTION, parent=division)
    project = make_org(tenant, "prj", OrgLevel.PROJECT, parent=section)

    return {"div": division, "sec": section, "prj": project}


def make_member(tenant: Tenant, org_unit: OrgUnit, code: str = "E1", user=None) -> OrgMember:
    return OrgMember.objects.create(
        tenant=tenant, org_unit=org_unit, employee_code=code, name=code, user=user
    )


def make_version(
    tenant: Tenant,
    fiscal_year: FiscalYear,
    kind: str = PlanKind.INITIAL,
    revision: int = 0,
    effective_from: date | None = None,
    status: str = PlanStatus.ACTIVE,
) -> PlanVersion:
    return PlanVersion.objects.create(
        tenant=tenant,
        fiscal_year=fiscal_year,
        kind=kind,
        revision=revision,
        effective_from=effective_from or fiscal_year.start_on,
        status=status,
    )


def add_plan(version: PlanVersion, org_unit: OrgUnit, month: date, revenue, member=None) -> PlanFigure:
    revenue = Decimal(revenue)

    return PlanFigure.objects.create(
        plan_version=version,
        org_unit=org_unit,
        member=member,
        month=month,
        revenue=revenue,
        gross_profit=revenue / 4,
        operating_profit=revenue / 10,
        source=FigureSource.CSV,
    )


def add_actual(
    fiscal_year: FiscalYear, org_unit: OrgUnit, month: date, revenue, member=None, source=FigureSource.CSV
) -> ActualFigure:
    revenue = Decimal(revenue)

    return ActualFigure.objects.create(
        tenant=org_unit.tenant,
        fiscal_year=fiscal_year,
        org_unit=org_unit,
        member=member,
        month=month,
        revenue=revenue,
        gross_profit=revenue / 4,
        operating_profit=revenue / 10,
        source=source,
    )
