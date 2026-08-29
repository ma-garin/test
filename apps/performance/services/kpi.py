"""KPI の達成状況。

達成判定を率だけで書かない。「低いほど良い」指標では実績が 0 のときに
`目標 ÷ 実績` がゼロ除算になるうえ、率の大小と良し悪しが逆転する。
判定は必ず方向を見た大小比較で行い、率は表示のためだけに出す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from apps.performance.constants import WARNING_RATIO, KpiAggregation, KpiDirection
from apps.performance.models import (
    FiscalYear,
    KpiDefinition,
    KpiResult,
    KpiTarget,
    OrgUnit,
    PlanVersion,
)
from apps.performance.services.aggregation import rate

#: 判定結果。画面のバッジ色（`badge g/a/r/n`）と対応させる。
STATUS_TONES = {
    "achieved": ("達成", "g"),
    "warning": ("要注意", "a"),
    "behind": ("未達", "r"),
    "no_target": ("目標未設定", "n"),
    "no_result": ("実績なし", "n"),
}


@dataclass(frozen=True)
class KpiStatus:
    kpi: KpiDefinition
    org_unit: OrgUnit
    target: Decimal | None
    actual: Decimal | None
    status: str = "no_target"

    @property
    def label(self) -> str:
        return STATUS_TONES[self.status][0]

    @property
    def tone(self) -> str:
        return STATUS_TONES[self.status][1]

    @property
    def achievement_ratio(self) -> Decimal | None:
        """表示用の達成率。方向に応じて分母分子を入れ替える。"""

        if self.target is None or self.actual is None:
            return None

        if self.kpi.higher_is_better:
            return rate(self.actual, self.target)

        # 低いほど良い指標。実績0は「完全達成」で、率としては表現しない。
        if not self.actual:
            return None

        return rate(self.target, self.actual)

    @property
    def gap(self) -> Decimal | None:
        if self.target is None or self.actual is None:
            return None

        return self.actual - self.target


def judge(kpi: KpiDefinition, target: Decimal | None, actual: Decimal | None) -> str:
    """達成・要注意・未達の判定。"""

    if target is None:
        return "no_target"

    if actual is None:
        return "no_result"

    if kpi.direction == KpiDirection.UP:
        if actual >= target:
            return "achieved"

        threshold = target * WARNING_RATIO / 100

        return "warning" if actual >= threshold else "behind"

    if actual <= target:
        return "achieved"

    # 低いほど良い指標は、超過幅で見る。目標の 10% 増しまでを要注意とする。
    threshold = target * (200 - WARNING_RATIO) / 100

    return "warning" if actual <= threshold else "behind"


def aggregate_results(kpi: KpiDefinition, values: list[tuple[date, Decimal]]) -> Decimal | None:
    """月次実績を年度実績へ畳む。指標ごとの集計方法に従う。"""

    if not values:
        return None

    if kpi.aggregation == KpiAggregation.SUM:
        return sum((value for _, value in values), Decimal("0"))

    if kpi.aggregation == KpiAggregation.AVERAGE:
        total = sum((value for _, value in values), Decimal("0"))

        return (total / len(values)).quantize(Decimal("0.01"))

    latest = max(values, key=lambda item: item[0])

    return latest[1]


def kpi_statuses(
    fiscal_year: FiscalYear,
    org_ids,
    months: list[date],
    plan_version: PlanVersion | None,
    member_id=None,
) -> list[KpiStatus]:
    """組織（または個人）ごとの KPI 達成状況。

    目標は計画版に紐づくため、期中変更で目標を置き直した場合は
    `plan_version` に現行版を渡す。版が無ければ目標は未設定として扱う。
    """

    org_ids = list(org_ids)

    if not org_ids:
        return []

    targets: dict[tuple, Decimal] = {}

    if plan_version is not None:
        target_queryset = KpiTarget.objects.filter(
            plan_version=plan_version, org_unit_id__in=org_ids
        ).select_related("kpi", "org_unit")

        target_queryset = (
            target_queryset.filter(member_id=member_id)
            if member_id
            else target_queryset.filter(member__isnull=True)
        )

        for target in target_queryset:
            targets[(target.kpi_id, target.org_unit_id)] = target.target_value

    result_queryset = KpiResult.objects.filter(
        fiscal_year=fiscal_year, org_unit_id__in=org_ids, month__in=months
    ).select_related("kpi", "org_unit")

    result_queryset = (
        result_queryset.filter(member_id=member_id)
        if member_id
        else result_queryset.filter(member__isnull=True)
    )

    collected: dict[tuple, list[tuple[date, Decimal]]] = {}
    kpis: dict = {}
    units: dict = {}

    for result in result_queryset:
        key = (result.kpi_id, result.org_unit_id)
        collected.setdefault(key, []).append((result.month, result.actual_value))
        kpis[result.kpi_id] = result.kpi
        units[result.org_unit_id] = result.org_unit

    # 目標だけあって実績が無い組み合わせも「未計測」として一覧に出す。
    for kpi_id, org_id in targets:
        collected.setdefault((kpi_id, org_id), [])

    # 目標だけがある組み合わせのために、KPI と組織を補完する。
    missing_kpi_ids = {kpi_id for kpi_id, _ in collected if kpi_id not in kpis}
    missing_org_ids = {org_id for _, org_id in collected if org_id not in units}

    for kpi in KpiDefinition.objects.filter(pk__in=missing_kpi_ids):
        kpis[kpi.pk] = kpi

    for unit in OrgUnit.objects.filter(pk__in=missing_org_ids):
        units[unit.pk] = unit

    statuses: list[KpiStatus] = []

    for (kpi_id, org_id), values in collected.items():
        kpi = kpis.get(kpi_id)
        unit = units.get(org_id)

        if kpi is None or unit is None:
            continue

        actual = aggregate_results(kpi, values)
        target = targets.get((kpi_id, org_id))

        statuses.append(
            KpiStatus(
                kpi=kpi,
                org_unit=unit,
                target=target,
                actual=actual,
                status=judge(kpi, target, actual),
            )
        )

    return sorted(statuses, key=lambda item: (item.kpi.code, item.org_unit.code))


@dataclass(frozen=True)
class KpiSummary:
    """達成状況の内訳。ダッシュボードの見出し数字に使う。"""

    statuses: list[KpiStatus]

    def count(self, status: str) -> int:
        return len([item for item in self.statuses if item.status == status])

    @property
    def total(self) -> int:
        return len(self.statuses)

    @property
    def achieved(self) -> int:
        return self.count("achieved")

    @property
    def warning(self) -> int:
        return self.count("warning")

    @property
    def behind(self) -> int:
        return self.count("behind")

    @property
    def unmeasured(self) -> int:
        return self.count("no_result") + self.count("no_target")

    @property
    def achieved_ratio(self) -> Decimal | None:
        measured = self.total - self.unmeasured

        return rate(Decimal(self.achieved), Decimal(measured)) if measured else None
