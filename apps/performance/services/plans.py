"""計画版（期初計画・期中変更計画）の解決。

**期中変更の適用ルール**

1. 適用中（`status=active`）の版だけを見る。作成中の版は集計へ出さない。
2. ある月に効く版は、`effective_from <= その月` を満たす版のうち最新のもの。
3. **その版に行が無い組織・メンバーは、直前の版の値を引き継ぐ。**

3 が肝で、期中変更は普通「変わった組織の分だけ」出てくる。行が無い組織を 0 と
みなすと、変更に無関係な組織の計画が期中で消える。逆に引き継ぎを黙ってやると
「どの版の数字を見ているか」が分からなくなるので、由来（`source_version`）を
値と一緒に返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from apps.performance.constants import PlanKind, PlanStatus
from apps.performance.models import FiscalYear, PlanFigure, PlanVersion


def active_versions(fiscal_year: FiscalYear) -> list[PlanVersion]:
    """適用中の計画版を、効き始める順に並べて返す。"""

    return list(
        PlanVersion.objects.filter(fiscal_year=fiscal_year, status=PlanStatus.ACTIVE).order_by(
            "effective_from", "revision"
        )
    )


def all_versions(fiscal_year: FiscalYear) -> list[PlanVersion]:
    return list(
        PlanVersion.objects.filter(fiscal_year=fiscal_year)
        .select_related("created_by")
        .order_by("revision")
    )


def initial_version(fiscal_year: FiscalYear) -> PlanVersion | None:
    return PlanVersion.objects.filter(fiscal_year=fiscal_year, kind=PlanKind.INITIAL).first()


def current_version(fiscal_year: FiscalYear, month: date | None = None) -> PlanVersion | None:
    """指定月に効いている計画版。月を省いたら最後に効く版。"""

    versions = active_versions(fiscal_year)

    if not versions:
        return None

    if month is None:
        return versions[-1]

    applicable = [version for version in versions if version.effective_from <= month]

    return applicable[-1] if applicable else None


def next_revision(fiscal_year: FiscalYear) -> int:
    """次に振る期中変更の改訂番号。"""

    last = (
        PlanVersion.objects.filter(fiscal_year=fiscal_year, kind=PlanKind.REVISED)
        .order_by("-revision")
        .first()
    )

    return (last.revision + 1) if last else 1


def ruling_versions(
    fiscal_year: FiscalYear, versions: list[PlanVersion] | None = None
) -> dict[date, PlanVersion]:
    """年度の各月を支配している計画版。行の有無とは無関係に版だけで決まる。"""

    versions = active_versions(fiscal_year) if versions is None else versions
    ruling: dict[date, PlanVersion] = {}

    for month in fiscal_year.months:
        applicable = [version for version in versions if version.effective_from <= month]

        if applicable:
            ruling[month] = applicable[-1]

    return ruling


def ruling_ranges(fiscal_year: FiscalYear) -> list[dict]:
    """「どの版がいつからいつまで効くか」を期間のまとまりで返す。

    月を12列並べた表は、読む側に「どこが変わったのか」を探させる。
    版が切り替わる境目だけを出す。
    """

    ruling = ruling_versions(fiscal_year)
    ranges: list[dict] = []

    for month in fiscal_year.months:
        version = ruling.get(month)

        if ranges and ranges[-1]["version"] == version:
            ranges[-1]["end"] = month
            continue

        ranges.append({"version": version, "start": month, "end": month})

    return ranges


@dataclass(frozen=True)
class EffectiveFigure:
    """ある月に効いている計画値と、その由来。

    `version` は値を供給した版、`ruling_version` はその月を支配している最新版。
    2つが違う行は「期中変更で触られなかったので前の版から引き継いだ値」で、
    画面ではそう明示する。どの版の数字を見ているか分からないまま
    差異を議論すると、変更したはずの数字が反映されていないと誤読される。
    """

    figure: PlanFigure
    version: PlanVersion
    ruling_version: PlanVersion

    @property
    def is_carried_over(self) -> bool:
        return self.version.pk != self.ruling_version.pk


def effective_figures(fiscal_year: FiscalYear, org_ids=None) -> dict[tuple, EffectiveFigure]:
    """`(org_id, member_id, month)` → その月に効いている計画値。

    版を効く順に上書きしていく。`month >= effective_from` の行だけを採るので、
    期中変更が過去月へ遡って効くことはない。
    """

    versions = active_versions(fiscal_year)

    if not versions:
        return {}

    queryset = PlanFigure.objects.filter(plan_version__in=versions)

    if org_ids is not None:
        queryset = queryset.filter(org_unit_id__in=list(org_ids))

    by_version: dict[str, list[PlanFigure]] = {}

    for figure in queryset:
        by_version.setdefault(str(figure.plan_version_id), []).append(figure)

    ruling = ruling_versions(fiscal_year, versions)
    effective: dict[tuple, EffectiveFigure] = {}

    # 版を効く順に上書きしていく。後続の版が触れていない組織・月は、
    # 前の版の値がそのまま残る（＝引き継ぎ）。
    for version in versions:
        for figure in by_version.get(str(version.pk), []):
            # 適用開始より前の月は、その版の担当範囲外。
            if figure.month < version.effective_from:
                continue

            key = (figure.org_unit_id, figure.member_id, figure.month)
            effective[key] = EffectiveFigure(
                figure=figure, version=version, ruling_version=version
            )

    return {
        key: EffectiveFigure(
            figure=item.figure,
            version=item.version,
            ruling_version=ruling.get(key[2], item.version),
        )
        for key, item in effective.items()
    }


def version_figures(version: PlanVersion, org_ids=None) -> dict[tuple, PlanFigure]:
    """単一の版に登録されている計画値。版そのものを見せる画面で使う。"""

    queryset = PlanFigure.objects.filter(plan_version=version)

    if org_ids is not None:
        queryset = queryset.filter(org_unit_id__in=list(org_ids))

    return {(f.org_unit_id, f.member_id, f.month): f for f in queryset}
