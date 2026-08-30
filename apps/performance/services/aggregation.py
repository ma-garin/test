"""計数の集計。組織ツリーの積み上げと、計画対比の計算。

**二重計上を避けるための約束**

組織の計数は次の2本立てで登録できる。

- 組織レベル … その組織に直接ぶら下がる計数（`member` が空の行）
- 個人レベル … 組織に所属するメンバーごとの計数（`member` を持つ行）

集計では **組織値 = 自組織の直接入力 + 配下組織の合計** とし、個人の値は足さない。
個人値は「組織値の内訳」とみなす。両方を足すと、個人別に配分を入れた組織だけ
金額が2倍になり、部の合計が課の合計と合わなくなる。

そのかわり、個人合計と組織値のずれ（`member_gap`）を算出して画面へ出す。
配分の入れ忘れ・入れ過ぎは、この差でしか気づけない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.performance.constants import ACHIEVED_RATIO, WARNING_RATIO
from apps.performance.models import ActualFigure, FiscalYear, OrgMember, OrgUnit, PlanVersion
from apps.performance.services import plans

ZERO = Decimal("0")


@dataclass(frozen=True)
class Amounts:
    """売上・粗利・利益の3点セット。率は保存せず、ここで導出する。"""

    revenue: Decimal = ZERO
    gross_profit: Decimal = ZERO
    operating_profit: Decimal = ZERO

    def __add__(self, other: Amounts) -> Amounts:
        return Amounts(
            revenue=self.revenue + other.revenue,
            gross_profit=self.gross_profit + other.gross_profit,
            operating_profit=self.operating_profit + other.operating_profit,
        )

    def __sub__(self, other: Amounts) -> Amounts:
        return Amounts(
            revenue=self.revenue - other.revenue,
            gross_profit=self.gross_profit - other.gross_profit,
            operating_profit=self.operating_profit - other.operating_profit,
        )

    @classmethod
    def of(cls, figure) -> Amounts:
        return cls(
            revenue=figure.revenue,
            gross_profit=figure.gross_profit,
            operating_profit=figure.operating_profit,
        )

    @property
    def is_empty(self) -> bool:
        return not (self.revenue or self.gross_profit or self.operating_profit)

    @property
    def gross_margin_rate(self) -> Decimal | None:
        return rate(self.gross_profit, self.revenue)

    @property
    def profit_rate(self) -> Decimal | None:
        return rate(self.operating_profit, self.revenue)


EMPTY = Amounts()


def rate(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """百分率。分母が0なら率は定義できないので None（0% と区別する）。"""

    if not denominator:
        return None

    return (Decimal(numerator) / Decimal(denominator) * 100).quantize(Decimal("0.01"))


def tone_for(ratio: Decimal | None) -> str:
    """達成率から表示トーンを決める。画面ごとに閾値を書かないための1本化。"""

    if ratio is None:
        return "n"

    if ratio >= ACHIEVED_RATIO:
        return "g"

    if ratio >= WARNING_RATIO:
        return "a"

    return "r"


@dataclass(frozen=True)
class Comparison:
    """計画と実績の対比。差異と達成率をまとめて持つ。"""

    plan: Amounts
    actual: Amounts

    @property
    def variance(self) -> Amounts:
        """実績 − 計画。マイナスが未達。"""

        return self.actual - self.plan

    @property
    def revenue_achievement(self) -> Decimal | None:
        return rate(self.actual.revenue, self.plan.revenue)

    @property
    def gross_profit_achievement(self) -> Decimal | None:
        return rate(self.actual.gross_profit, self.plan.gross_profit)

    @property
    def profit_achievement(self) -> Decimal | None:
        return rate(self.actual.operating_profit, self.plan.operating_profit)

    @property
    def profit_rate_gap(self) -> Decimal | None:
        """利益率の差（ポイント）。率どうしは引き算しないと意味が出ない。"""

        planned, actual = self.plan.profit_rate, self.actual.profit_rate

        if planned is None or actual is None:
            return None

        return actual - planned

    @property
    def tone(self) -> str:
        return tone_for(self.revenue_achievement)

    @property
    def profit_tone(self) -> str:
        return tone_for(self.profit_achievement)


@dataclass
class AmountIndex:
    """`(組織, 月)` と `(メンバー, 月)` で引ける金額の索引。

    集計のたびに SQL を撃つと、部→課→PJ の3階層で N+1 が3乗になる。
    年度分を1回読んでメモリ上で畳む。
    """

    org: dict[tuple, Amounts] = field(default_factory=dict)
    member: dict[tuple, Amounts] = field(default_factory=dict)

    def add(self, org_id, member_id, month: date, amounts: Amounts) -> None:
        bucket = self.member if member_id else self.org
        key = (member_id, month) if member_id else (org_id, month)
        bucket[key] = bucket.get(key, EMPTY) + amounts

    def org_amount(self, org_id, months: list[date]) -> Amounts:
        total = EMPTY

        for month in months:
            total = total + self.org.get((org_id, month), EMPTY)

        return total

    def member_amount(self, member_id, months: list[date]) -> Amounts:
        total = EMPTY

        for month in months:
            total = total + self.member.get((member_id, month), EMPTY)

        return total


def plan_index(fiscal_year: FiscalYear, org_ids=None) -> AmountIndex:
    """期中変更を反映した「現行計画」の索引。"""

    index = AmountIndex()

    for (org_id, member_id, month), values in plans.effective_amounts(
        fiscal_year, org_ids
    ).items():
        index.add(org_id, member_id, month, Amounts(*values))

    return index


def version_index(version: PlanVersion, org_ids=None) -> AmountIndex:
    """特定の版だけを見た索引。期初計画との比較に使う。"""

    index = AmountIndex()

    for (org_id, member_id, month), values in plans.version_amounts(version, org_ids).items():
        index.add(org_id, member_id, month, Amounts(*values))

    return index


def actual_index(fiscal_year: FiscalYear, org_ids=None) -> AmountIndex:
    index = AmountIndex()
    queryset = ActualFigure.objects.filter(fiscal_year=fiscal_year)

    if org_ids is not None:
        queryset = queryset.filter(org_unit_id__in=list(org_ids))

    # 実績も値だけで足りる。1万行規模ではモデルインスタンス生成が効いてくる。
    for org_id, member_id, month, revenue, gross, profit in queryset.values_list(
        "org_unit_id", "member_id", "month", "revenue", "gross_profit", "operating_profit"
    ):
        index.add(org_id, member_id, month, Amounts(revenue, gross, profit))

    return index


def descendants_map(units: list[OrgUnit]) -> dict:
    """組織ID → 自分と配下すべてのID。

    渡された集合の中だけで木を閉じる。参照権限で切られた組織が集合から外れて
    いれば、その配下も自動的に集計へ入らない（見えない組織の数字が親の合計に
    混ざることを防ぐ）。
    """

    children: dict = {}

    for unit in units:
        children.setdefault(unit.parent_id, []).append(unit)

    known = {unit.pk for unit in units}
    result: dict = {}

    def collect(unit: OrgUnit) -> list:
        if unit.pk in result:
            return result[unit.pk]

        ids = [unit.pk]

        for child in children.get(unit.pk, []):
            ids.extend(collect(child))

        result[unit.pk] = ids

        return ids

    for unit in units:
        collect(unit)

    # 親が集合外の組織は、その組織自身が根として扱われる。
    return {unit_id: ids for unit_id, ids in result.items() if unit_id in known}


@dataclass(frozen=True)
class OrgSummary:
    """1組織ぶんの集計結果。"""

    unit: OrgUnit
    own_plan: Amounts
    own_actual: Amounts
    total_plan: Amounts
    total_actual: Amounts
    total_initial: Amounts
    member_plan: Amounts
    member_actual: Amounts

    @property
    def comparison(self) -> Comparison:
        return Comparison(plan=self.total_plan, actual=self.total_actual)

    @property
    def initial_comparison(self) -> Comparison:
        """期初計画に対する実績。期中で計画を下げた場合はここで露見する。"""

        return Comparison(plan=self.total_initial, actual=self.total_actual)

    @property
    def plan_revision(self) -> Amounts:
        """現行計画 − 期初計画。期中変更でどれだけ動かしたか。"""

        return self.total_plan - self.total_initial

    @property
    def member_gap(self) -> Amounts:
        """個人配分の合計 − 組織の直接入力値。"""

        return self.member_actual - self.own_actual

    @property
    def member_over_allocated(self) -> bool:
        """個人の合計が組織の金額を超えているか。

        *不足* は警告しない。個人別に管理するのは一部のメンバーだけ、という
        運用が普通で、毎回警告を出すと本当に見るべき行が埋もれる。
        超過は入力ミスか二重計上のどちらかなので、こちらだけ知らせる。
        """

        return self.member_actual.revenue > self.own_actual.revenue

    @property
    def has_member_breakdown(self) -> bool:
        return not (self.member_plan.is_empty and self.member_actual.is_empty)


@dataclass(frozen=True)
class Report:
    """画面が使う集計結果一式。"""

    fiscal_year: FiscalYear
    months: list[date]
    summaries: dict
    plan: AmountIndex
    actual: AmountIndex
    initial: AmountIndex
    descendants: dict

    def for_unit(self, unit: OrgUnit) -> OrgSummary | None:
        return self.summaries.get(unit.pk if hasattr(unit, "pk") else unit)

    def for_months(self, months: list[date]) -> Report:
        """同じ索引のまま、対象期間だけを差し替えた版を返す。

        サマリは累計、グラフは年度全体と、同じデータを違う期間で見るだけ。
        `build_report` を呼び直すと 1 万行の読み込みと集計をもう一度やることになる。

        期間が変われば組織別の合計も変わるので `summaries` は引き継がない
        （`for_unit` は None を返す）。月次行を出す用途にだけ使う。
        """

        return Report(
            fiscal_year=self.fiscal_year,
            months=months,
            summaries={},
            plan=self.plan,
            actual=self.actual,
            initial=self.initial,
            descendants=self.descendants,
        )

    def totals(self, units: list[OrgUnit]) -> Comparison:
        """複数組織の合計。親子が混ざっても二重に数えないよう、根だけを足す。"""

        ids = {unit.pk for unit in units}
        roots = [unit for unit in units if unit.parent_id not in ids]

        plan, actual = EMPTY, EMPTY

        for unit in roots:
            summary = self.summaries.get(unit.pk)

            if summary is None:
                continue

            plan = plan + summary.total_plan
            actual = actual + summary.total_actual

        return Comparison(plan=plan, actual=actual)

    def monthly_rows(self, unit: OrgUnit) -> list[MonthlyRow]:
        ids = self.descendants.get(unit.pk, [unit.pk])
        rows: list[MonthlyRow] = []
        cumulative_plan, cumulative_actual = EMPTY, EMPTY

        for month in self.months:
            plan, actual, initial = EMPTY, EMPTY, EMPTY

            for org_id in ids:
                plan = plan + self.plan.org_amount(org_id, [month])
                actual = actual + self.actual.org_amount(org_id, [month])
                initial = initial + self.initial.org_amount(org_id, [month])

            cumulative_plan = cumulative_plan + plan
            cumulative_actual = cumulative_actual + actual

            rows.append(
                MonthlyRow(
                    month=month,
                    plan=plan,
                    actual=actual,
                    initial=initial,
                    cumulative_plan=cumulative_plan,
                    cumulative_actual=cumulative_actual,
                )
            )

        return rows


@dataclass(frozen=True)
class MonthlyRow:
    month: date
    plan: Amounts
    actual: Amounts
    initial: Amounts
    cumulative_plan: Amounts
    cumulative_actual: Amounts

    @property
    def comparison(self) -> Comparison:
        return Comparison(plan=self.plan, actual=self.actual)

    @property
    def cumulative(self) -> Comparison:
        return Comparison(plan=self.cumulative_plan, actual=self.cumulative_actual)

    @property
    def has_data(self) -> bool:
        return not (self.plan.is_empty and self.actual.is_empty)


@dataclass(frozen=True)
class MemberSummary:
    member: OrgMember
    plan: Amounts
    actual: Amounts

    @property
    def comparison(self) -> Comparison:
        return Comparison(plan=self.plan, actual=self.actual)


def build_report(
    fiscal_year: FiscalYear,
    units: list[OrgUnit],
    months: list[date] | None = None,
    members: list[OrgMember] | None = None,
) -> Report:
    """組織集合に対する年度集計。`months` を絞れば期首からの累計になる。"""

    months = months if months is not None else fiscal_year.months
    org_ids = [unit.pk for unit in units]

    plan = plan_index(fiscal_year, org_ids)
    actual = actual_index(fiscal_year, org_ids)

    initial_version = plans.initial_version(fiscal_year)
    initial = version_index(initial_version, org_ids) if initial_version else AmountIndex()

    descendants = descendants_map(units)

    members_by_org: dict = {}

    if members is not None:
        for member in members:
            members_by_org.setdefault(member.org_unit_id, []).append(member)

    # 自分ぶんの金額は組織ごとに一度だけ出す。
    own: dict = {
        unit.pk: (
            plan.org_amount(unit.pk, months),
            actual.org_amount(unit.pk, months),
            initial.org_amount(unit.pk, months),
        )
        for unit in units
    }

    # 配下を含む合計は、子の合計を足し上げて作る。
    # 親ごとに配下すべてを走査し直すと、同じ組織を何度も足すことになる
    # （部6・課30・プロジェクト150 で 516 回 → 186 回）。
    children: dict = {}

    for unit in units:
        children.setdefault(unit.parent_id, []).append(unit)

    totals: dict = {}

    def total_of(unit: OrgUnit) -> tuple:
        cached = totals.get(unit.pk)

        if cached is not None:
            return cached

        own_plan, own_actual, own_initial = own[unit.pk]
        sum_plan, sum_actual, sum_initial = own_plan, own_actual, own_initial

        for child in children.get(unit.pk, []):
            child_plan, child_actual, child_initial = total_of(child)
            sum_plan = sum_plan + child_plan
            sum_actual = sum_actual + child_actual
            sum_initial = sum_initial + child_initial

        totals[unit.pk] = (sum_plan, sum_actual, sum_initial)

        return totals[unit.pk]

    for unit in units:
        total_of(unit)

    summaries: dict = {}

    for unit in units:
        own_plan, own_actual, _ = own[unit.pk]
        total_plan, total_actual, total_initial = totals[unit.pk]

        member_plan, member_actual = EMPTY, EMPTY

        for member in members_by_org.get(unit.pk, []):
            member_plan = member_plan + plan.member_amount(member.pk, months)
            member_actual = member_actual + actual.member_amount(member.pk, months)

        summaries[unit.pk] = OrgSummary(
            unit=unit,
            own_plan=own_plan,
            own_actual=own_actual,
            total_plan=total_plan,
            total_actual=total_actual,
            total_initial=total_initial,
            member_plan=member_plan,
            member_actual=member_actual,
        )

    return Report(
        fiscal_year=fiscal_year,
        months=months,
        summaries=summaries,
        plan=plan,
        actual=actual,
        initial=initial,
        descendants=descendants,
    )


def member_summaries(
    report: Report, members: list[OrgMember], months: list[date] | None = None
) -> list[MemberSummary]:
    months = months if months is not None else report.months

    return [
        MemberSummary(
            member=member,
            plan=report.plan.member_amount(member.pk, months),
            actual=report.actual.member_amount(member.pk, months),
        )
        for member in members
    ]
