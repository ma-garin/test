"""手入力の保存。

CSV 取込と同じ書き込み口をここに置き、画面から入る値も取込から入る値も
同じ検証・同じ upsert を通す。入口が2つに割れると、片方だけ丸め方や
重複判定が違う、という食い違いが必ず起きる。

**空欄と 0 の区別**

グリッド入力では、空欄は「値なし」、`0` は「0 と置いた」を意味する。空欄で
既存行を消さないと、間違って入れた数字を消す手段が画面に無くなる。逆に
空欄を 0 として保存すると、計画の引き継ぎ（`services/plans`）が「0 と置かれた」
と解釈して前の版の値を上書きしてしまう。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from apps.performance.constants import FigureSource
from apps.performance.models import (
    ActualFigure,
    FiscalYear,
    KpiDefinition,
    KpiResult,
    KpiTarget,
    OrgMember,
    OrgUnit,
    PlanVersion,
)
from apps.performance.services.aggregation import Amounts


@dataclass
class WriteResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0

    def merge(self, other: WriteResult) -> WriteResult:
        self.created += other.created
        self.updated += other.updated
        self.deleted += other.deleted
        self.unchanged += other.unchanged

        return self

    @property
    def touched(self) -> int:
        return self.created + self.updated + self.deleted


def _apply(model, lookup: dict, values: dict | None, *, protect_manual: bool = False) -> WriteResult:
    """1行ぶんの upsert。`values` が None なら削除。"""

    existing = model.objects.filter(**lookup).first()

    if values is None:
        if existing is None:
            return WriteResult()

        existing.delete()

        return WriteResult(deleted=1)

    if existing is None:
        model.objects.create(**lookup, **values)

        return WriteResult(created=1)

    # 手入力を CSV で黙って上書きしない。上書きするかは呼び出し側が決める。
    if protect_manual and existing.source == FigureSource.MANUAL:
        return WriteResult(unchanged=1)

    changed = [name for name, value in values.items() if getattr(existing, name) != value]

    if not changed:
        return WriteResult(unchanged=1)

    for name, value in values.items():
        setattr(existing, name, value)

    existing.save(update_fields=[*values.keys(), "updated_at"])

    return WriteResult(updated=1)


def save_plan_amounts(
    *,
    plan_version: PlanVersion,
    org_unit: OrgUnit,
    member: OrgMember | None,
    month: date,
    amounts: Amounts | None,
    source: str = FigureSource.MANUAL,
    note: str = "",
    protect_manual: bool = False,
) -> WriteResult:
    from apps.performance.models import PlanFigure

    lookup = {
        "plan_version": plan_version,
        "org_unit": org_unit,
        "member": member,
        "month": month,
    }
    values = (
        None
        if amounts is None
        else {
            "revenue": amounts.revenue,
            "gross_profit": amounts.gross_profit,
            "operating_profit": amounts.operating_profit,
            "source": source,
            "note": note,
        }
    )

    return _apply(PlanFigure, lookup, values, protect_manual=protect_manual)


def save_actual_amounts(
    *,
    fiscal_year: FiscalYear,
    org_unit: OrgUnit,
    member: OrgMember | None,
    month: date,
    amounts: Amounts | None,
    source: str = FigureSource.MANUAL,
    note: str = "",
    user=None,
    protect_manual: bool = False,
) -> WriteResult:
    lookup = {
        "fiscal_year": fiscal_year,
        "org_unit": org_unit,
        "member": member,
        "month": month,
    }
    values = (
        None
        if amounts is None
        else {
            "tenant_id": org_unit.tenant_id,
            "revenue": amounts.revenue,
            "gross_profit": amounts.gross_profit,
            "operating_profit": amounts.operating_profit,
            "source": source,
            "note": note,
            "updated_by": user,
        }
    )

    return _apply(ActualFigure, lookup, values, protect_manual=protect_manual)


def save_kpi_target(
    *,
    kpi: KpiDefinition,
    plan_version: PlanVersion,
    org_unit: OrgUnit,
    member: OrgMember | None,
    target_value: Decimal | None,
    note: str = "",
) -> WriteResult:
    lookup = {
        "kpi": kpi,
        "plan_version": plan_version,
        "org_unit": org_unit,
        "member": member,
    }
    values = None if target_value is None else {"target_value": target_value, "note": note}

    return _apply(KpiTarget, lookup, values)


def save_kpi_result(
    *,
    kpi: KpiDefinition,
    fiscal_year: FiscalYear,
    org_unit: OrgUnit,
    member: OrgMember | None,
    month: date,
    actual_value: Decimal | None,
    source: str = FigureSource.MANUAL,
    note: str = "",
    protect_manual: bool = False,
) -> WriteResult:
    lookup = {
        "kpi": kpi,
        "fiscal_year": fiscal_year,
        "org_unit": org_unit,
        "member": member,
        "month": month,
    }
    values = (
        None
        if actual_value is None
        else {
            "tenant_id": org_unit.tenant_id,
            "actual_value": actual_value,
            "source": source,
            "note": note,
        }
    )

    return _apply(KpiResult, lookup, values, protect_manual=protect_manual)
