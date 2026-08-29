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
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.constants import Action
from apps.accounts.services import permissions
from apps.core.pagination import page_window, paginate, query_without_page
from apps.performance import selectors
from apps.performance.constants import ImportKind, PlanKind, PlanStatus
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
from apps.performance.services import aggregation, csv_io, entry, plans
from apps.performance.services import kpi as kpi_service
from apps.performance.services.calendar import format_month, parse_month

DASHBOARD_URL = "performance:dashboard"

#: マスタ系の取込。値の入力より強い権限（管理）を要求する。
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
    members = list(selectors.members_for(request.user, request.tenant))
    report = aggregation.build_report(fiscal_year, units, months, members)

    roots = _roots(units)
    root_children = [unit for unit in units if unit.parent_id in {root.pk for root in roots}]

    statuses = kpi_service.kpi_statuses(
        fiscal_year,
        [unit.pk for unit in units],
        months,
        plans.current_version(fiscal_year, upto),
    )

    breakdown = root_children or roots

    return render(
        request,
        "pages/perf_dashboard.html",
        {
            "page_title": "計数ダッシュボード",
            "page_subtitle": f"{fiscal_year.name}／{format_month(fiscal_year.start_on)}〜"
            f"{format_month(upto)} 累計",
            "units": units,
            "roots": roots,
            "summaries": [report.for_unit(unit) for unit in breakdown],
            "root_summaries": [report.for_unit(unit) for unit in roots],
            "total": report.totals(roots),
            "months": months,
            "upto": upto,
            "all_months": fiscal_year.months,
            "kpi_summary": kpi_service.KpiSummary(statuses=statuses),
            "kpi_statuses": statuses[:8],
            "attention": [
                summary
                for summary in (report.for_unit(unit) for unit in breakdown)
                if summary is not None and summary.comparison.tone == "r"
            ],
            **_year_context(request, fiscal_year),
        },
    )


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

    return render(
        request,
        "pages/perf_org_detail.html",
        {
            "page_title": f"{unit.name}",
            "page_subtitle": f"{unit.get_level_display()}／{fiscal_year.name}",
            "unit": unit,
            "summary": summary,
            "monthly_rows": report.monthly_rows(unit),
            "child_summaries": [report.for_unit(child) for child in children],
            "member_summaries": aggregation.member_summaries(report, members, months),
            "kpi_statuses": statuses,
            "kpi_summary": kpi_service.KpiSummary(statuses=statuses),
            "can_edit": selectors.can_edit_org(request.user, unit),
            "months": months,
            "upto": upto,
            "all_months": fiscal_year.months,
            **_year_context(request, fiscal_year),
        },
    )


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

    rows = []

    for month in months:
        plan = report.plan.member_amount(member.pk, [month])
        actual = report.actual.member_amount(member.pk, [month])
        rows.append({"month": month, "comparison": aggregation.Comparison(plan=plan, actual=actual)})

    summary = aggregation.member_summaries(report, [member], months)[0]

    return render(
        request,
        "pages/perf_member_detail.html",
        {
            "page_title": member.name,
            "page_subtitle": f"{member.org_unit.name}／{fiscal_year.name}",
            "member": member,
            "summary": summary,
            "org_summary": report.for_unit(member.org_unit),
            "rows": rows,
            "can_edit": selectors.can_edit_org(request.user, member.org_unit),
            "months": months,
            "upto": upto,
            "all_months": fiscal_year.months,
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
    ruling = plans.ruling_versions(fiscal_year)

    report = aggregation.build_report(fiscal_year, units)
    roots = _roots(units)

    version_totals = []

    for version in versions:
        index = aggregation.version_index(version, [unit.pk for unit in units])
        total = aggregation.EMPTY

        for unit in units:
            total = total + index.org_amount(unit.pk, fiscal_year.months)

        version_totals.append({"version": version, "total": total})

    return render(
        request,
        "pages/perf_plan_list.html",
        {
            "page_title": "計数計画",
            "page_subtitle": "期初計画は上書きせず、見直しは期中変更計画として積みます。",
            "versions": version_totals,
            "ruling": [
                {"month": month, "version": ruling.get(month)} for month in fiscal_year.months
            ],
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
        return None, None

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

    context = {
        "page_title": "計数入力",
        "page_subtitle": "CSV 取込と同じ保存処理を通ります。空欄は「値なし」として既存値を削除します。",
        "units": units,
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

    page = paginate(KpiDefinition.objects.filter(tenant=request.tenant), request)

    return render(
        request,
        "pages/perf_kpi_list.html",
        {
            "page_title": "KPI管理",
            "page_subtitle": "目標は計画版に紐づきます。期中変更で目標を置き直せます。",
            "definitions": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "statuses": statuses,
            "kpi_summary": kpi_service.KpiSummary(statuses=statuses),
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
    definition = KpiDefinition.objects.filter(
        pk=request.GET.get("kpi") or request.POST.get("kpi"), tenant=request.tenant
    ).first()
    version = plans.current_version(fiscal_year) if fiscal_year else None

    context = {
        "page_title": "KPI入力",
        "page_subtitle": "目標値は選択中の計画版に保存されます。",
        "units": units,
        "unit": unit,
        "member": member,
        "definition": definition,
        "definitions": KpiDefinition.objects.filter(tenant=request.tenant, is_active=True),
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

    form = CsvImportForm(
        request.POST or None, request.FILES or None, tenant=request.tenant
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
    """組織・メンバー・年度のマスタ管理。"""

    units = _visible_units(request)
    fiscal_year = selectors.resolve_fiscal_year(request)

    return render(
        request,
        "pages/perf_org_list.html",
        {
            "page_title": "組織・マスタ管理",
            "page_subtitle": "部・課・プロジェクトの階層と、所属メンバーを管理します。",
            "units": units,
            "members": selectors.members_for(request.user, request.tenant),
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
