"""計数・目標管理の画面。

利用者はラインマネージャーで、見たいのは「自分の組織が計画に対してどうか」。
そのため入口（ダッシュボード）は必ず *自分の担当組織* から始まり、上位組織へは
遡れない。参照範囲の判定は `selectors` に集約し、ビューでは書かない。

編集系はすべて `selectors.can_edit_org` を通す。表示を隠すだけでは、POST を
直接投げられたときに防げない。
"""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.constants import Action
from apps.accounts.services import permissions
from apps.core.pagination import page_window, paginate, query_without_page
from apps.performance import selectors
from apps.performance.constants import ImportKind, OrgLevel, PlanKind, PlanStatus
from apps.performance.forms import (
    CsvImportForm,
    FiscalYearForm,
    KpiDefinitionForm,
    KpiEntryForm,
    MonthlyFigureForm,
    OrgMemberForm,
    OrgUnitForm,
    PlanVersionForm,
)
from apps.performance.models import (
    ImportBatch,
    KpiDefinition,
    KpiResult,
    KpiTarget,
    OrgMember,
    OrgUnit,
    PlanVersion,
)
from apps.performance.services import (
    aggregation,
    csv_io,
    entry,
    plans,
    presentation,
)
from apps.performance.services import (
    chart as chart_service,
)
from apps.performance.services import kpi as kpi_service
from apps.performance.services.calendar import format_month, parse_month, shift_year

DASHBOARD_URL = "performance:dashboard"

#: マスタ系の取込。値の入力より強い権限（管理）を要求する。
#: ダッシュボードの手当カードに出す件数。ここを超えた分は
#: 「手当が要る組織だけ」表示へ送る（件数と行き先を必ず添える）。
ATTENTION_ON_DASHBOARD = 4

#: 組織詳細の KPI カードに出す件数。部の下の課すべての KPI が集まるため、
#: 上限を置かないと 1 画面を KPI だけで埋める。
KPI_ALERTS_ON_DETAIL = 2

MASTER_IMPORT_KINDS = (ImportKind.ORG_UNIT, ImportKind.MEMBER)


def _require_year(request: HttpRequest):
    """対象年度。1つも無ければ作成を促す。"""

    year = selectors.resolve_fiscal_year(request)

    if year is None:
        messages.warning(request, "年度が登録されていません。まず年度を作成してください。")

    return year


def _months_upto(fiscal_year, request: HttpRequest) -> tuple[list, date]:
    """期首から対象月までの並びと、その対象月。

    既定は今日が年度内ならその月、年度外なら期末月。累計を「今日時点」で
    読めるようにするためで、未来月の空欄が達成率を押し下げないようにする。
    """

    months = fiscal_year.months
    requested = parse_month(request.GET.get("upto", ""))

    if requested is None or requested not in months:
        today = timezone.localdate().replace(day=1)
        requested = today if today in months else months[-1]

    return [month for month in months if month <= requested], requested


def _visible_units(request) -> list[OrgUnit]:
    return list(selectors.org_units_for(request.user, request.tenant))


def _tree_order(units: list[OrgUnit]) -> list[tuple[OrgUnit, int]]:
    """親のすぐ下に子が並ぶ順で、深さを添えて返す。

    階層でまとめて並べると、部・課・プロジェクトが3つの塊になり、
    どのプロジェクトがどの課の下かを上位組織の列から目で辿ることになる。
    """

    children: dict = {}

    for unit in units:
        children.setdefault(unit.parent_id, []).append(unit)

    ids = {unit.pk for unit in units}
    ordered: list[tuple[OrgUnit, int]] = []

    def walk(parent_id, depth: int) -> None:
        for unit in children.get(parent_id, []):
            ordered.append((unit, depth))
            walk(unit.pk, depth + 1)

    # 可視集合の外に親を持つ組織も、この画面では起点として扱う。
    for parent_id in [None, *[key for key in children if key is not None and key not in ids]]:
        walk(parent_id, 0)

    return ordered


def _org_groups(units: list[OrgUnit]) -> list[dict]:
    """組織セレクト用に、上位組織ごとの塊へまとめる。

    実運用では 186 件が1本のドロップダウンに並ぶ。名前が似ている課が
    続くため、平らな並びだと目的の組織を目で追えない。
    optgroup にすると、部・課の見出しが手がかりになる。
    """

    by_id = {unit.pk: unit for unit in units}
    groups: dict = {}

    for unit in units:
        parent = by_id.get(unit.parent_id)
        key = parent.pk if parent is not None else None
        label = parent.name if parent is not None else "上位組織"
        groups.setdefault(key, {"label": label, "items": []})["items"].append(unit)

    # 上位組織そのものの塊を先に出す。掘る前に部・課を選べる。
    ordered = sorted(
        groups.values(),
        key=lambda group: (group["label"] != "上位組織", group["label"]),
    )

    return ordered


def _scope_label(roots: list[OrgUnit]) -> str:
    """見出しに出す対象範囲。

    全社を見る立場だと 6 部が連結され、見出しが1行を超える。
    3 つを超えたら先頭と件数で表す。
    """

    names = [unit.name for unit in roots]

    if len(names) <= 3:
        return "／".join(names) or "対象組織なし"

    return f"{names[0]} ほか {len(names) - 1} 部門"


def _roots(units: list[OrgUnit]) -> list[OrgUnit]:
    """可視集合の中で親を持たない組織。ここが画面の起点になる。"""

    ids = {unit.pk for unit in units}

    return [unit for unit in units if unit.parent_id not in ids]


def _year_context(request, fiscal_year) -> dict:
    return {
        "fiscal_year": fiscal_year,
        "fiscal_years": selectors.fiscal_years_for(request.tenant),
        "current_version": plans.current_version(fiscal_year) if fiscal_year else None,
        "initial_version": plans.initial_version(fiscal_year) if fiscal_year else None,
    }


def _prior_year_report(fiscal_year, units, months, members=None):
    """前年同期のレポート。

    対象期間（`months`）をそのまま1年ずらして前年度に当てはめる。前年度が
    登録されていない、または対応する月が前年度の範囲外（決算期を変更した年度
    など）なら None を返す。「前年比較ができない」と「前年比0%」を区別するため、
    呼び出し側は None を必ず「未確認」として扱う。
    """

    prior_year = fiscal_year.previous

    if prior_year is None:
        return None

    prior_months = [shift_year(month, -1) for month in months]
    prior_months = [month for month in prior_months if prior_year.contains(month)]

    if not prior_months:
        return None

    return aggregation.build_report(prior_year, units, prior_months, members or [])


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """計数ダッシュボード。担当組織の年度累計と、直下組織の達成状況。"""

    fiscal_year = _require_year(request)
    units = _visible_units(request)

    if fiscal_year is None or not units:
        return render(
            request,
            "pages/perf_dashboard.html",
            {
                "page_title": "計数ダッシュボード",
                "page_subtitle": "組織別の計画と実績、KPI 達成状況を1画面で確認します。",
                "units": units,
                **_year_context(request, fiscal_year),
            },
        )

    months, upto = _months_upto(fiscal_year, request)
    # ダッシュボードは組織単位でしか数字を出さない。個人 650 名ぶんの
    # 月次を集計に含めても画面には出ず、待ち時間だけが増える。
    report = aggregation.build_report(fiscal_year, units, months)
    metric = presentation.metric_from(request)

    roots = _roots(units)
    root_ids = {root.pk for root in roots}
    children = [unit for unit in units if unit.parent_id in root_ids]
    breakdown = children or roots

    total = report.totals(roots)

    prior_report = _prior_year_report(fiscal_year, units, months)
    prior_total = prior_report.totals(roots).actual if prior_report is not None else None
    unit = presentation.unit_from(request)

    def _line(summary):
        return presentation.org_line(
            summary.unit.name,
            summary.comparison,
            url=reverse("performance:org_detail", args=[summary.unit.pk]),
            note=summary.unit.get_level_display(),
            unit=unit,
        )

    lines = [
        _line(summary)
        for summary in (report.for_unit(unit) for unit in breakdown)
        if summary is not None
    ]
    focus = request.GET.get("focus") or ""

    # 手当の対象は、1階層下ではなく見えている組織すべてから選ぶ。
    # 課で平均すると配下の落ち込みが消える（課90.6%の中に78%のプロジェクトが
    # いても、課の行しか見なければ「あと少し」に見える）。
    # 深い階層の未達こそ、まずダッシュボードに出す必要がある。
    def _deep_line(summary):
        # 「車載ECU 結合検証」だけでは、どの課の下かが読み手に伝わらない。
        parent = summary.unit.parent

        return presentation.org_line(
            summary.unit.name,
            summary.comparison,
            url=reverse("performance:org_detail", args=[summary.unit.pk]),
            note=f"{parent.name}／{summary.unit.get_level_display()}"
            if parent is not None
            else summary.unit.get_level_display(),
            unit=unit,
        )

    deep_lines = [
        _deep_line(summary)
        for summary in (report.for_unit(item) for item in units if item not in roots)
        if summary is not None
    ]

    # 実運用では手当が要る組織が 100 件を超える。全部並べても上から
    # 読む人はいないので、額の大きい順に上位だけをここへ出し、
    # 残りは同じ画面の「手当が要る組織だけ」表示へ送る。
    # 隠して終わりにしない（件数と行き先を必ず添える）ことが条件。
    attention_all = sorted(
        (line for line in deep_lines if line.needs_attention),
        key=lambda line: (-line.shortfall, line.worst_achievement),
    )
    attention_lines = attention_all[:ATTENTION_ON_DASHBOARD]

    # 組織別の表は既定で1階層下。focus=attention のときは手当が要る
    # 組織を階層に関係なく並べ、ページ送りで全件たどれるようにする。
    org_rows = attention_all if focus == "attention" else lines
    # 全件表示では手当カードが件数だけになるぶん、表を長く取れる。
    # 全件表示ではグラフと手当カードを出さないぶん、表を長く取れる。
    org_page = paginate(org_rows, request, per_page=5 if focus == "attention" else 4)

    statuses = kpi_service.kpi_statuses(
        fiscal_year,
        [unit.pk for unit in units],
        months,
        plans.current_version(fiscal_year, upto),
    )

    # グラフと月次表は年度全体で描く。累計だけだと「どの月で落ちたか」が見えない。
    # 索引は累計用と同じものを使い回す（作り直すと1万行を二度読むことになる）。
    full_report = report.for_months(fiscal_year.months)
    monthly = _combined_monthly_rows(full_report, roots)
    metric = presentation.metric_from(request)

    return render(
        request,
        "pages/perf_dashboard.html",
        {
            "page_title": "計数ダッシュボード",
            "page_subtitle": f"{fiscal_year.name}　{format_month(fiscal_year.start_on)}〜"
            f"{format_month(upto)} の累計",
            "units": units,
            "scope_label": _scope_label(roots),
            "summary_rows": presentation.summary_rows(total, prior_total, unit),
            "total": total,
            "has_prior_year": prior_report is not None,
            "unit": unit,
            "unit_label": presentation.UNIT_LABELS[unit],
            "unit_decimals": presentation.unit_decimals(unit),
            "unit_tabs": presentation.unit_tabs(unit),
            "lines": org_page.object_list,
            "focus": focus,
            "attention_total": len(attention_all),
            "attention_hidden": max(len(attention_all) - len(attention_lines), 0),
            "org_page": org_page,
            "org_page_window": page_window(org_page),
            "org_page_query": query_without_page(request),
            # 手当が要る行を先に出す。全部を読ませてから探させない。
            # 組織とKPIを合わせて3件まで。並びは手当の要る順なので、
            # 落ちるのは相対的に軽い項目になる。全件は各画面で見る。
            "attention_lines": attention_lines,
            "metric": metric,
            "metric_tabs": presentation.metric_tabs(metric),
            "chart": chart_service.monthly_chart(monthly, metric),
            "monthly_rows": monthly,
            "month_rows": [
                presentation.row_from(f"{row.month:%Y/%m}", row.comparison, metric, unit=unit)
                for row in monthly
            ],
            "kpi_summary": kpi_service.KpiSummary(statuses=statuses),
            # 1画面へ収めるため件数を絞る。並びは手当の要る順なので、
            # 落ちるのは相対的に軽い項目になる。全件は KPI 管理で見る。
            "kpi_alerts": [item for item in statuses if item.status in ("behind", "warning")][:2],
            "summaries": [report.for_unit(unit) for unit in breakdown],
            "months": months,
            "upto": upto,
            "all_months": fiscal_year.months,
            "extra_query": {"metric": metric},
            **_year_context(request, fiscal_year),
        },
    )


def _combined_monthly_rows(report, units):
    """複数の根組織をまとめた月次行。親子が混ざっても二重に数えない。"""

    if not units:
        return []

    if len(units) == 1:
        return report.monthly_rows(units[0])

    combined = None

    for unit in units:
        rows = report.monthly_rows(unit)

        if combined is None:
            combined = rows
            continue

        combined = [
            aggregation.MonthlyRow(
                month=left.month,
                plan=left.plan + right.plan,
                actual=left.actual + right.actual,
                initial=left.initial + right.initial,
                cumulative_plan=left.cumulative_plan + right.cumulative_plan,
                cumulative_actual=left.cumulative_actual + right.cumulative_actual,
            )
            for left, right in zip(combined, rows, strict=True)
        ]

    return combined or []


@login_required
def org_detail(request: HttpRequest, pk) -> HttpResponse:
    """組織の月次推移と、配下組織・所属メンバーの内訳。"""

    units = _visible_units(request)
    unit = next((item for item in units if str(item.pk) == str(pk)), None)

    if unit is None:
        # 参照できない組織は「無い」として扱う。存在の有無を漏らさない。
        raise PermissionDenied("この組織を参照する権限がありません。")

    fiscal_year = _require_year(request)

    if fiscal_year is None:
        return redirect(DASHBOARD_URL)

    months, upto = _months_upto(fiscal_year, request)
    members = list(selectors.members_for(request.user, request.tenant, [unit.pk]))
    report = aggregation.build_report(fiscal_year, units, months, members)
    summary = report.for_unit(unit)

    children = [item for item in units if item.parent_id == unit.pk]
    statuses = kpi_service.kpi_statuses(
        fiscal_year,
        report.descendants.get(unit.pk, [unit.pk]),
        months,
        plans.current_version(fiscal_year, upto),
    )

    metric = presentation.metric_from(request)
    display_unit = presentation.unit_from(request)
    monthly_rows = report.monthly_rows(unit)
    member_rows = aggregation.member_summaries(report, members, months)

    # グラフは年度全体。累計期間だけだと、どの月で崩れたのかが読めない。
    # 索引は累計用を使い回す（作り直すと同じ行をもう一度読むことになる）。
    full_monthly = report.for_months(fiscal_year.months).monthly_rows(unit)

    prior_report = _prior_year_report(fiscal_year, units, months)
    prior_summary = prior_report.for_unit(unit) if prior_report is not None else None
    prior_actual = prior_summary.total_actual if prior_summary is not None else None

    # 課の下にはプロジェクトが 5 件前後、部の下には課が 5 件前後つく。
    # 実運用では配下が二桁になる組織もあるため、ページ送りで扱う。
    # 並びは手当が要るものが先（対計画比の低い順）。
    child_lines = sorted(
        (
            presentation.org_line(
                child.unit.name,
                child.comparison,
                url=reverse("performance:org_detail", args=[child.unit.pk]),
                note=child.unit.get_level_display(),
                unit=display_unit,
            )
            for child in (report.for_unit(item) for item in children)
            if child is not None
        ),
        key=lambda line: line.worst_achievement,
    )
    child_page = paginate(child_lines, request, per_page=3)
    kpi_alerts = [item for item in statuses if item.status in ("behind", "warning")]

    return render(
        request,
        "pages/perf_org_detail.html",
        {
            "page_title": f"{unit.name}",
            "page_subtitle": f"{unit.get_level_display()}　{fiscal_year.name}　"
            f"{format_month(fiscal_year.start_on)}〜{format_month(upto)} の累計",
            "unit": unit,
            "summary": summary,
            "headline": presentation.headline_from(summary.comparison, metric),
            "summary_rows": presentation.summary_rows(summary.comparison, prior_actual, display_unit),
            "has_prior_year": prior_report is not None,
            "display_unit": display_unit,
            "unit_label": presentation.UNIT_LABELS[display_unit],
            "unit_decimals": presentation.unit_decimals(display_unit),
            "unit_tabs": presentation.unit_tabs(display_unit),
            "chart": chart_service.monthly_chart(full_monthly, metric),
            "metric": metric,
            "metric_tabs": presentation.metric_tabs(metric),
            "month_rows": [
                presentation.row_from(f"{row.month:%Y/%m}", row.comparison, metric, unit=display_unit)
                for row in monthly_rows
            ],
            "child_lines": child_page.object_list,
            "child_total": len(child_lines),
            "child_page": child_page,
            "child_page_window": page_window(child_page),
            "child_page_query": query_without_page(request),
            "member_lines": [
                presentation.org_line(
                    row.member.name,
                    row.comparison,
                    url=reverse("performance:member_detail", args=[row.member.pk]),
                    note=row.member.employee_code,
                    unit=display_unit,
                )
                for row in member_rows
            ],
            "member_rows": [
                presentation.row_from(
                    row.member.name,
                    row.comparison,
                    metric,
                    url=reverse("performance:member_detail", args=[row.member.pk]),
                    note=row.member.employee_code,
                    unit=display_unit,
                )
                for row in member_rows
            ],
            "monthly_rows": monthly_rows,
            "child_summaries": [report.for_unit(child) for child in children],
            "member_summaries": member_rows,
            "kpi_statuses": statuses,
            # 部の下には課が並び、その全KPIがここへ集まる。件数を出したうえで
            # 上位だけを見せ、残りは KPI 管理で追えるようにする。
            "kpi_alerts": kpi_alerts[:KPI_ALERTS_ON_DETAIL],
            "kpi_alert_total": len(kpi_alerts),
            "kpi_summary": kpi_service.KpiSummary(statuses=statuses),
            "can_edit": selectors.can_edit_org(request.user, unit),
            "months": months,
            "upto": upto,
            "all_months": fiscal_year.months,
            "extra_query": {"metric": metric},
            **_year_context(request, fiscal_year),
        },
    )


def _member_monthly_rows(report, member, months) -> list:
    """個人の月次行。組織と同じ形にして、表・グラフの描画を共通化する。"""

    rows: list = []
    cumulative_plan, cumulative_actual = aggregation.EMPTY, aggregation.EMPTY

    for month in months:
        plan = report.plan.member_amount(member.pk, [month])
        actual = report.actual.member_amount(member.pk, [month])
        cumulative_plan = cumulative_plan + plan
        cumulative_actual = cumulative_actual + actual

        rows.append(
            aggregation.MonthlyRow(
                month=month,
                plan=plan,
                actual=actual,
                initial=aggregation.EMPTY,
                cumulative_plan=cumulative_plan,
                cumulative_actual=cumulative_actual,
            )
        )

    return rows


@login_required
def member_detail(request: HttpRequest, pk) -> HttpResponse:
    """個人の計数。組織の内訳としての位置づけを併記する。"""

    member = get_object_or_404(
        selectors.members_for(request.user, request.tenant), pk=pk
    )
    fiscal_year = _require_year(request)

    if fiscal_year is None:
        return redirect(DASHBOARD_URL)

    months, upto = _months_upto(fiscal_year, request)
    units = _visible_units(request)
    report = aggregation.build_report(fiscal_year, units, months, [member])

    metric = presentation.metric_from(request)
    display_unit = presentation.unit_from(request)
    rows = _member_monthly_rows(report, member, months)
    summary = aggregation.member_summaries(report, [member], months)[0]

    # グラフは年度全体。累計期間だけだと、どの月で崩れたのかが読めない。
    full_report = report.for_months(fiscal_year.months)
    full_rows = _member_monthly_rows(full_report, member, fiscal_year.months)

    prior_report = _prior_year_report(fiscal_year, units, months, [member])
    prior_actual = (
        prior_report.actual.member_amount(member.pk, prior_report.months)
        if prior_report is not None
        else None
    )

    return render(
        request,
        "pages/perf_member_detail.html",
        {
            "page_title": member.name,
            "page_subtitle": f"{member.org_unit.name}　{fiscal_year.name}　"
            f"{format_month(fiscal_year.start_on)}〜{format_month(upto)} の累計",
            "member": member,
            "summary": summary,
            "headline": presentation.headline_from(summary.comparison, metric),
            "summary_rows": presentation.summary_rows(summary.comparison, prior_actual, display_unit),
            "has_prior_year": prior_report is not None,
            "display_unit": display_unit,
            "unit_label": presentation.UNIT_LABELS[display_unit],
            "unit_decimals": presentation.unit_decimals(display_unit),
            "unit_tabs": presentation.unit_tabs(display_unit),
            "chart": chart_service.monthly_chart(full_rows, metric),
            "metric": metric,
            "metric_tabs": presentation.metric_tabs(metric),
            "month_rows": [
                presentation.row_from(f"{row.month:%Y/%m}", row.comparison, metric, unit=display_unit)
                for row in rows
            ],
            "org_summary": report.for_unit(member.org_unit),
            "rows": rows,
            "can_edit": selectors.can_edit_org(request.user, member.org_unit),
            "months": months,
            "upto": upto,
            "all_months": fiscal_year.months,
            "extra_query": {"metric": metric},
            **_year_context(request, fiscal_year),
        },
    )


@login_required
def plan_list(request: HttpRequest) -> HttpResponse:
    """期初計画と期中変更計画の一覧。どの月にどの版が効くかを併記する。"""

    fiscal_year = _require_year(request)

    if fiscal_year is None:
        return render(
            request,
            "pages/perf_plan_list.html",
            {"page_title": "計数計画", **_year_context(request, fiscal_year)},
        )

    units = _visible_units(request)
    versions = plans.all_versions(fiscal_year)

    report = aggregation.build_report(fiscal_year, units)
    roots = _roots(units)

    ranges = plans.ruling_ranges(fiscal_year)

    # 版が2つとも「適用中」に見えると、どちらの数字で達成率が出ているか分からない。
    # 実際に効いている月の範囲を版ごとに畳んで、行に添える。
    spans: dict = {}

    for item in ranges:
        version = item["version"]

        if version is None:
            continue

        span = spans.get(version.pk)
        spans[version.pk] = (
            {"start": item["start"], "end": item["end"]}
            if span is None
            else {"start": span["start"], "end": item["end"]}
        )

    version_totals = []

    for version in versions:
        index = aggregation.version_index(version, [unit.pk for unit in units])
        total = aggregation.EMPTY

        for unit in units:
            total = total + index.org_amount(unit.pk, fiscal_year.months)

        version_totals.append({"version": version, "total": total, "span": spans.get(version.pk)})

    return render(
        request,
        "pages/perf_plan_list.html",
        {
            "page_title": "計数計画",
            "page_subtitle": "期初計画は書き換えず、見直しは新しい版として追加します。",
            "versions": version_totals,
            "ruling_ranges": ranges,
            "current_total": report.totals(roots),
            "can_manage": permissions.can(request.user, Action.EDIT),
            **_year_context(request, fiscal_year),
        },
    )


@login_required
def plan_create(request: HttpRequest) -> HttpResponse:
    fiscal_year = _require_year(request)

    if fiscal_year is None:
        return redirect(DASHBOARD_URL)

    permissions.require(request.user, Action.EDIT)

    has_initial = plans.initial_version(fiscal_year) is not None
    form = PlanVersionForm(
        request.POST or None, fiscal_year=fiscal_year, has_initial=has_initial
    )

    if request.method == "POST" and form.is_valid():
        version = form.save(commit=False)
        version.tenant = request.tenant
        version.fiscal_year = fiscal_year
        version.created_by = request.user

        if version.kind == PlanKind.REVISED:
            version.revision = plans.next_revision(fiscal_year)

        version.save()
        messages.success(request, f"{version} を作成しました。")

        return redirect("performance:plan_list")

    return render(
        request,
        "pages/perf_plan_form.html",
        {
            "page_title": "計画版の作成",
            "page_subtitle": "期中の見直しは、期初計画を書き換えず新しい版として登録します。",
            "form": form,
            "fiscal_year": fiscal_year,
            "has_initial": has_initial,
        },
    )


@login_required
def plan_edit(request: HttpRequest, pk) -> HttpResponse:
    version = get_object_or_404(PlanVersion, pk=pk, tenant=request.tenant)
    permissions.require(request.user, Action.EDIT)

    form = PlanVersionForm(
        request.POST or None,
        instance=version,
        fiscal_year=version.fiscal_year,
        has_initial=plans.initial_version(version.fiscal_year) is not None,
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{version} を更新しました。")

        return redirect("performance:plan_list")

    return render(
        request,
        "pages/perf_plan_form.html",
        {
            "page_title": f"{version} の編集",
            "form": form,
            "version": version,
            "fiscal_year": version.fiscal_year,
        },
    )


@login_required
@require_POST
def plan_activate(request: HttpRequest, pk) -> HttpResponse:
    """計画版を適用中にする。ここを通って初めて集計へ出る。"""

    version = get_object_or_404(PlanVersion, pk=pk, tenant=request.tenant)
    permissions.require(request.user, Action.EDIT)

    version.status = PlanStatus.ACTIVE
    version.save(update_fields=["status", "updated_at"])
    messages.success(
        request,
        f"{version} を適用しました。{format_month(version.effective_from)} 以降の計画が"
        "この版へ切り替わります。",
    )

    return redirect("performance:plan_list")


def _entry_target(request, units: list[OrgUnit]):
    """入力対象の組織とメンバーを URL パラメータから決める。

    参照できない組織を指定された場合は 404 にする。「選択画面に戻す」だと、
    権限が無いのか組織が無いのかを利用者が区別できず、POST が黙って
    無視されたようにも見える。他アプリと同じく、越境は存在しない扱いにする。
    """

    org_id = request.GET.get("org") or request.POST.get("org")

    if not org_id:
        # 指定が無ければ、編集できる末端の組織を開く。
        # 部を既定にすると全欄が空の表が出る（部の計数は課の積み上げで、
        # 直接は持たない）。利用者からは「データが無い」のか
        # 「入れる場所が違う」のか区別がつかない。
        parents = {item.parent_id for item in units}
        # 組織ごとに can_edit_org を呼ぶと、1件ずつ権限とテナントを引きにいき、
        # 186 組織で 380 本近いクエリになる。編集できる ID は一度だけ引く。
        managed = selectors.managed_org_ids(request.user, request.tenant)
        editable = [item for item in units if item.pk in managed]

        return (
            next((item for item in editable if item.pk not in parents), None)
            or next(iter(editable), None),
            None,
        )

    unit = next((item for item in units if str(item.pk) == str(org_id)), None)

    if unit is None:
        raise Http404("組織が見つかりません。")

    member_id = request.GET.get("member") or request.POST.get("member")
    member = None

    if member_id:
        member = OrgMember.objects.filter(
            pk=member_id, org_unit=unit, tenant=request.tenant
        ).first()

        if member is None:
            raise PermissionDenied("このメンバーは対象組織に所属していません。")

    return unit, member


@login_required
def figure_entry(request: HttpRequest) -> HttpResponse:
    """計数の手入力。組織（または個人）×月のグリッドで1年分をまとめて保存する。"""

    fiscal_year = _require_year(request)
    units = _visible_units(request)
    unit, member = _entry_target(request, units) if fiscal_year else (None, None)
    mode = request.GET.get("mode") or request.POST.get("mode") or "actual"
    mode = mode if mode in ("plan", "actual") else "actual"

    version = None

    if mode == "plan":
        version_id = request.GET.get("plan") or request.POST.get("plan")
        version = (
            PlanVersion.objects.filter(pk=version_id, tenant=request.tenant).first()
            if version_id
            else plans.current_version(fiscal_year)
        )

    has_children = bool(unit and any(item.parent_id == unit.pk for item in units))

    context = {
        "page_title": "計数入力",
        "has_children": has_children,
        "page_subtitle": "CSV 取込と同じ保存処理を通ります。空欄は「値なし」として既存値を削除します。",
        "units": units,
        "org_groups": _org_groups(units),
        "unit": unit,
        "member": member,
        "mode": mode,
        "version": version,
        "versions": PlanVersion.objects.filter(fiscal_year=fiscal_year).exclude(
            status=PlanStatus.ARCHIVED
        )
        if fiscal_year
        else PlanVersion.objects.none(),
        "members": selectors.members_for(request.user, request.tenant, [unit.pk] if unit else None),
        **_year_context(request, fiscal_year),
    }

    if fiscal_year is None or unit is None:
        return render(request, "pages/perf_entry.html", context)

    if not selectors.can_edit_org(request.user, unit):
        raise PermissionDenied("この組織の計数を編集する権限がありません。")

    if mode == "plan" and version is None:
        messages.warning(request, "先に計画版を作成してください。")

        return redirect("performance:plan_list")

    months = fiscal_year.months
    initial = _entry_initial(fiscal_year, version, unit, member, months, mode)
    form = MonthlyFigureForm(
        request.POST or None, months=months, initial_amounts=initial
    )

    if request.method == "POST" and form.is_valid():
        result = entry.WriteResult()

        for month, amounts in form.amounts().items():
            if mode == "plan":
                result.merge(
                    entry.save_plan_amounts(
                        plan_version=version,
                        org_unit=unit,
                        member=member,
                        month=month,
                        amounts=amounts,
                    )
                )
            else:
                result.merge(
                    entry.save_actual_amounts(
                        fiscal_year=fiscal_year,
                        org_unit=unit,
                        member=member,
                        month=month,
                        amounts=amounts,
                        user=request.user,
                    )
                )

        messages.success(
            request,
            f"保存しました（新規 {result.created} / 更新 {result.updated} / 削除 {result.deleted}）。",
        )

        return redirect(
            f"{request.path}?year={fiscal_year.code}&mode={mode}&org={unit.pk}"
            + (f"&member={member.pk}" if member else "")
            + (f"&plan={version.pk}" if version else "")
        )

    context["form"] = form

    return render(request, "pages/perf_entry.html", context)


def _entry_initial(fiscal_year, version, unit, member, months, mode) -> dict:
    """グリッドの初期値。保存済みの値をそのまま出す。"""

    if mode == "plan":
        figures = plans.version_figures(version, [unit.pk])

        return {
            month: aggregation.Amounts.of(figures[(unit.pk, member.pk if member else None, month)])
            for month in months
            if (unit.pk, member.pk if member else None, month) in figures
        }

    from apps.performance.models import ActualFigure

    queryset = ActualFigure.objects.filter(
        fiscal_year=fiscal_year, org_unit=unit, month__in=months
    )
    queryset = queryset.filter(member=member) if member else queryset.filter(member__isnull=True)

    return {figure.month: aggregation.Amounts.of(figure) for figure in queryset}


@login_required
def kpi_list(request: HttpRequest) -> HttpResponse:
    """KPI 定義と、担当組織での達成状況。"""

    fiscal_year = _require_year(request)
    units = _visible_units(request)
    statuses = []

    if fiscal_year is not None and units:
        months, upto = _months_upto(fiscal_year, request)
        statuses = kpi_service.kpi_statuses(
            fiscal_year,
            [unit.pk for unit in units],
            months,
            plans.current_version(fiscal_year, upto),
        )

    order = {"behind": 0, "warning": 1, "no_result": 2, "no_target": 3, "achieved": 4}
    statuses = sorted(statuses, key=lambda item: (order[item.status], item.kpi.code))
    definitions = KpiDefinition.objects.filter(tenant=request.tenant)

    # 実運用では 3指標 × 30課 で 90 行を超える。全部を1ページに並べると
    # 1.5 画面ぶんになり、手当が要る行を探すのにスクロールが要る。
    # 集計は絞り込み前の全件で出す（絞ると全体像が読めなくなる）。
    summary = kpi_service.KpiSummary(statuses=statuses)
    kpi_filter = request.GET.get("kpi") or ""
    status_filter = request.GET.get("status") or ""

    if kpi_filter:
        statuses = [item for item in statuses if item.kpi.code == kpi_filter]

    if status_filter:
        statuses = [item for item in statuses if item.status == status_filter]

    page = paginate(statuses, request, per_page=8)

    return render(
        request,
        "pages/perf_kpi_list.html",
        {
            "page_title": "KPI管理",
            "page_subtitle": "目標は計画の版ごとに持ちます。期中の見直しで置き直せます。",
            "definitions": definitions,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "statuses": page.object_list,
            "status_count": len(statuses),
            "kpi_filter": kpi_filter,
            "status_filter": status_filter,
            "status_choices": (
                ("behind", "未達"),
                ("warning", "あと少し"),
                ("achieved", "達成"),
                ("no_result", "未計測"),
            ),
            "kpi_summary": summary,
            "units": units,
            "can_manage": permissions.can(request.user, Action.MANAGE),
            **_year_context(request, fiscal_year),
        },
    )


@login_required
def kpi_create(request: HttpRequest) -> HttpResponse:
    permissions.require(request.user, Action.MANAGE)

    form = KpiDefinitionForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        definition = form.save(commit=False)
        definition.tenant = request.tenant
        definition.save()
        messages.success(request, f"KPI「{definition.name}」を登録しました。")

        return redirect("performance:kpi_list")

    return render(
        request,
        "pages/perf_form.html",
        {
            "page_title": "KPIの登録",
            "form": form,
            "back_url": "performance:kpi_list",
            "back_label": "KPI管理へ戻る",
        },
    )


@login_required
def kpi_edit(request: HttpRequest, pk) -> HttpResponse:
    definition = get_object_or_404(KpiDefinition, pk=pk, tenant=request.tenant)
    permissions.require(request.user, Action.MANAGE)

    form = KpiDefinitionForm(request.POST or None, instance=definition)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"KPI「{definition.name}」を更新しました。")

        return redirect("performance:kpi_list")

    return render(
        request,
        "pages/perf_form.html",
        {
            "page_title": f"{definition.name} の編集",
            "form": form,
            "back_url": "performance:kpi_list",
            "back_label": "KPI管理へ戻る",
        },
    )


@login_required
def kpi_entry(request: HttpRequest) -> HttpResponse:
    """KPI の目標値と月次実績の手入力。"""

    fiscal_year = _require_year(request)
    units = _visible_units(request)
    unit, member = _entry_target(request, units) if fiscal_year else (None, None)
    definitions = KpiDefinition.objects.filter(tenant=request.tenant, is_active=True)
    kpi_id = request.GET.get("kpi") or request.POST.get("kpi")
    definition = (
        definitions.filter(pk=kpi_id).first()
        if kpi_id
        # 未指定なら先頭を開く。空の画面から始めると、入力の前に必ず一手増える。
        else definitions.first()
    )
    version = plans.current_version(fiscal_year) if fiscal_year else None

    context = {
        "page_title": "KPI入力",
        "page_subtitle": "目標値は選択中の計画版に保存されます。",
        "units": units,
        "org_groups": _org_groups(units),
        "unit": unit,
        "member": member,
        "definition": definition,
        "definitions": definitions,
        "version": version,
        "members": selectors.members_for(request.user, request.tenant, [unit.pk] if unit else None),
        **_year_context(request, fiscal_year),
    }

    if fiscal_year is None or unit is None or definition is None:
        return render(request, "pages/perf_kpi_entry.html", context)

    if not selectors.can_edit_org(request.user, unit):
        raise PermissionDenied("この組織の KPI を編集する権限がありません。")

    if version is None:
        messages.warning(request, "適用中の計画版がありません。先に計画版を作成・適用してください。")

        return redirect("performance:plan_list")

    months = fiscal_year.months
    target = KpiTarget.objects.filter(
        kpi=definition, plan_version=version, org_unit=unit, member=member
    ).first()
    results = KpiResult.objects.filter(
        kpi=definition, fiscal_year=fiscal_year, org_unit=unit, member=member, month__in=months
    )

    form = KpiEntryForm(
        request.POST or None,
        months=months,
        initial_target=target.target_value if target else None,
        initial_results={result.month: result.actual_value for result in results},
    )

    if request.method == "POST" and form.is_valid():
        result = entry.save_kpi_target(
            kpi=definition,
            plan_version=version,
            org_unit=unit,
            member=member,
            target_value=form.cleaned_data.get("target_value"),
        )

        for month, value in form.results().items():
            result.merge(
                entry.save_kpi_result(
                    kpi=definition,
                    fiscal_year=fiscal_year,
                    org_unit=unit,
                    member=member,
                    month=month,
                    actual_value=value,
                )
            )

        messages.success(request, f"KPI「{definition.name}」を保存しました。")

        return redirect(
            f"{request.path}?year={fiscal_year.code}&org={unit.pk}&kpi={definition.pk}"
            + (f"&member={member.pk}" if member else "")
        )

    context["form"] = form

    return render(request, "pages/perf_kpi_entry.html", context)


@login_required
def import_view(request: HttpRequest) -> HttpResponse:
    """CSV 取込。取込条件は画面で選び、ファイルには持たせない。

    履歴の参照は権限を問わない（何が入ったかは全員が追えるべき）。
    書き込みだけを `Action.EDIT` で守る。
    """

    can_import = permissions.can(request.user, Action.EDIT)
    can_import_master = permissions.can(request.user, Action.MANAGE)

    if request.method == "POST" and not can_import:
        raise PermissionDenied("CSV を取り込む権限がありません。")

    current_year = selectors.resolve_fiscal_year(request)
    form = CsvImportForm(
        request.POST or None,
        request.FILES or None,
        tenant=request.tenant,
        current_year=current_year,
        current_version=plans.current_version(current_year) if current_year else None,
    )
    outcome = None

    if request.method == "POST" and form.is_valid():
        # 組織・メンバーはマスタ。値の入力（EDIT）とは別に管理権限を要る形にする。
        # 画面側のマスタ編集と権限を揃えないと、CSV が抜け道になる。
        if form.cleaned_data["kind"] in MASTER_IMPORT_KINDS and not can_import_master:
            raise PermissionDenied("組織・メンバーのマスタを取り込む権限がありません。")

        upload = form.cleaned_data["csv_file"]
        context = csv_io.ImportContext(
            tenant=request.tenant,
            user=request.user,
            fiscal_year=form.cleaned_data.get("fiscal_year"),
            plan_version=form.cleaned_data.get("plan_version"),
            editable_org_ids=selectors.managed_org_ids(request.user, request.tenant),
            overwrite_manual=form.cleaned_data["overwrite_manual"],
        )

        try:
            outcome, batch = csv_io.run_import(
                kind=form.cleaned_data["kind"],
                raw=upload.read(),
                filename=upload.name,
                context=context,
                skip_errors=form.cleaned_data["skip_errors"],
            )
        except csv_io.CsvFormatError as error:
            messages.error(request, f"ファイルを読めませんでした: {error}")
        else:
            if outcome.applied:
                messages.success(
                    request,
                    f"取込完了（新規 {outcome.created} / 更新 {outcome.updated} / "
                    f"エラー {outcome.error_count}）。",
                )
            else:
                messages.error(
                    request,
                    f"{outcome.error_count} 行にエラーがあったため、1行も取り込んでいません。"
                    "内容を直すか「エラー行を除いて取り込む」を選んでください。",
                )

            return redirect("performance:import_detail", pk=batch.pk)

    page = paginate(ImportBatch.objects.filter(tenant=request.tenant), request)

    return render(
        request,
        "pages/perf_import.html",
        {
            "page_title": "CSV取込",
            "page_subtitle": "組織・メンバー・計数・KPI を CSV で取り込みます。",
            "form": form,
            "can_import": can_import,
            "can_import_master": can_import_master,
            "batches": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "kinds": [
                {"value": value, "label": label, "columns": csv_io.COLUMNS[value]}
                for value, label in ImportKind.choices
            ],
            # 種別によって要る条件が違う。画面から要らない欄を消すために渡す。
            # 実績の取込で「対象計画版」が並んでいると、要るのかどうか迷わせる。
            "needs_year": list(CsvImportForm.NEEDS_YEAR),
            "needs_version": list(CsvImportForm.NEEDS_VERSION),
        },
    )


@login_required
def import_detail(request: HttpRequest, pk) -> HttpResponse:
    batch = get_object_or_404(ImportBatch, pk=pk, tenant=request.tenant)

    return render(
        request,
        "pages/perf_import_detail.html",
        {
            "page_title": f"{batch.get_kind_display()}の取込結果",
            "page_subtitle": f"{batch.filename}／{batch.created_at:%Y-%m-%d %H:%M}",
            "batch": batch,
        },
    )


@login_required
def csv_template(request: HttpRequest, kind: str) -> HttpResponse:
    """記入例つきテンプレートのダウンロード。"""

    if kind not in csv_io.COLUMNS:
        raise PermissionDenied("不明な取込種別です。")

    return _csv_response(csv_io.template_csv(kind), f"template_{kind}.csv")


@login_required
def csv_export(request: HttpRequest, kind: str) -> HttpResponse:
    """現在値の書き出し。取込と同じ列で出し、往復編集できるようにする。"""

    if kind not in csv_io.COLUMNS:
        raise PermissionDenied("不明な取込種別です。")

    fiscal_year = _require_year(request)

    if fiscal_year is None:
        return redirect(DASHBOARD_URL)

    version_id = request.GET.get("plan")
    version = (
        PlanVersion.objects.filter(pk=version_id, tenant=request.tenant).first()
        if version_id
        else plans.current_version(fiscal_year)
    )

    body = csv_io.export_csv(
        kind,
        fiscal_year=fiscal_year,
        plan_version=version,
        org_ids=selectors.visible_org_ids(request.user, request.tenant),
    )

    return _csv_response(body, f"{kind}_{fiscal_year.code}.csv")


def _csv_response(body: str, filename: str) -> HttpResponse:
    # Excel が文字化けしないよう BOM を付ける。付けないと日本語列名が壊れる。
    response = HttpResponse(body.encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    return response


@login_required
def org_list(request: HttpRequest) -> HttpResponse:
    """組織・メンバー・年度のマスタ管理。

    実運用では組織 186・要員 650 になる。全件を1ページに並べると
    32画面ぶんの縦になり、目的の行にたどり着けない。
    組織と要員はタブで分け、絞り込みとページ送りで扱う。
    """

    units = _visible_units(request)
    fiscal_year = selectors.resolve_fiscal_year(request)
    tab = request.GET.get("tab") or "orgs"
    tab = tab if tab in ("orgs", "members") else "orgs"
    keyword = (request.GET.get("q") or "").strip()
    level = request.GET.get("level") or ""

    org_rows = _tree_order(units)

    if level:
        org_rows = [(unit, depth) for unit, depth in org_rows if unit.level == level]

    if keyword:
        needle = keyword.lower()
        org_rows = [
            (unit, depth)
            for unit, depth in org_rows
            if needle in unit.name.lower() or needle in unit.code.lower()
        ]

    members = selectors.members_for(request.user, request.tenant)

    if keyword:
        members = members.filter(
            Q(name__icontains=keyword)
            | Q(employee_code__icontains=keyword)
            | Q(org_unit__name__icontains=keyword)
        )

    page = paginate(org_rows if tab == "orgs" else members, request, per_page=14)

    return render(
        request,
        "pages/perf_org_list.html",
        {
            "page_title": "組織・マスタ管理",
            "page_subtitle": "部・課・プロジェクトの階層と、所属メンバーを管理します。",
            "tab": tab,
            "keyword": keyword,
            "level": level,
            "levels": OrgLevel.choices,
            "org_count": len(org_rows),
            "member_count": members.count(),
            "units": page.object_list if tab == "orgs" else [],
            "members": page.object_list if tab == "members" else [],
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "can_manage": permissions.can(request.user, Action.MANAGE),
            "managed_ids": selectors.managed_org_ids(request.user, request.tenant),
            **_year_context(request, fiscal_year),
        },
    )


@login_required
def org_form(request: HttpRequest, pk=None) -> HttpResponse:
    permissions.require(request.user, Action.MANAGE)

    units = _visible_units(request)
    instance = None

    if pk is not None:
        instance = next((item for item in units if str(item.pk) == str(pk)), None)

        if instance is None:
            raise PermissionDenied("この組織を編集する権限がありません。")

    form = OrgUnitForm(
        request.POST or None,
        instance=instance,
        tenant=request.tenant,
        units=selectors.org_units_for(request.user, request.tenant),
    )

    if request.method == "POST" and form.is_valid():
        unit = form.save(commit=False)
        unit.tenant = request.tenant
        unit.full_clean(exclude=["tenant"], validate_unique=False)
        unit.save()
        messages.success(request, f"組織「{unit.name}」を保存しました。")

        return redirect("performance:org_list")

    return render(
        request,
        "pages/perf_form.html",
        {
            "page_title": "組織の編集" if instance else "組織の登録",
            "form": form,
            "back_url": "performance:org_list",
            "back_label": "組織管理へ戻る",
        },
    )


@login_required
def member_form(request: HttpRequest, pk=None) -> HttpResponse:
    permissions.require(request.user, Action.MANAGE)

    instance = None

    if pk is not None:
        instance = get_object_or_404(selectors.members_for(request.user, request.tenant), pk=pk)

    form = OrgMemberForm(
        request.POST or None,
        instance=instance,
        tenant=request.tenant,
        units=selectors.org_units_for(request.user, request.tenant),
    )

    if request.method == "POST" and form.is_valid():
        member = form.save(commit=False)
        member.tenant = request.tenant
        member.save()
        messages.success(request, f"メンバー「{member.name}」を保存しました。")

        return redirect("performance:org_list")

    return render(
        request,
        "pages/perf_form.html",
        {
            "page_title": "メンバーの編集" if instance else "メンバーの登録",
            "form": form,
            "back_url": "performance:org_list",
            "back_label": "組織管理へ戻る",
        },
    )


@login_required
def fiscal_year_form(request: HttpRequest, pk=None) -> HttpResponse:
    permissions.require(request.user, Action.MANAGE)

    instance = None

    if pk is not None:
        instance = get_object_or_404(selectors.fiscal_years_for(request.tenant), pk=pk)

    form = FiscalYearForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        year = form.save(commit=False)
        year.tenant = request.tenant
        year.save()

        if year.is_current:
            # 今期は1つだけ。複数あると既定の年度が不定になる。
            selectors.fiscal_years_for(request.tenant).exclude(pk=year.pk).update(
                is_current=False
            )

        messages.success(request, f"年度「{year.name}」を保存しました。")

        return redirect("performance:org_list")

    return render(
        request,
        "pages/perf_form.html",
        {
            "page_title": "年度の編集" if instance else "年度の登録",
            "form": form,
            "back_url": "performance:org_list",
            "back_label": "組織管理へ戻る",
        },
    )
