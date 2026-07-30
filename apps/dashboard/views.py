"""管制ダッシュボード配下の画面。

参照は `apps.dashboard.selectors`、集計は `apps.dashboard.services` に置き、
ビューは「絞り込み条件を受け取って、組み立て済みの表示データを渡す」だけにする。
テナント分離は `projects_for()` を必ず入口に通すことで担保している。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.dashboard import selectors
from apps.dashboard.forms import InterventionDecisionForm
from apps.dashboard.models import InterventionProposal
from apps.dashboard.services.decisions import (
    build_change_report,
    build_intervention_report,
    build_risk_report,
)
from apps.dashboard.services.interventions import (
    AlreadyDecidedError,
    decide_intervention,
    is_pending,
)
from apps.dashboard.services.kpi import build_derived_rows, build_kpi_report
from apps.dashboard.services.overview import build_overview
from apps.dashboard.services.progress import build_progress_report
from apps.dashboard.services.quality import build_quality_report
from apps.dashboard.services.tasks import TaskFilters, build_task_board
from apps.projects.selectors import projects_for


def _projects(request: HttpRequest):
    """この利用者が参照できる案件。全画面の入口。"""

    return projects_for(request.user, getattr(request, "tenant", None))


@login_required
def control(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/control_dashboard.html",
        {"overview": build_overview(_projects(request)), "page_title": "管制ダッシュボード"},
    )


@login_required
def tasks(request: HttpRequest) -> HttpResponse:
    filters = TaskFilters(
        owner=request.GET.get("owner", "").strip(),
        status=request.GET.get("status", ""),
        priority=request.GET.get("priority", ""),
        due=request.GET.get("due", ""),
        progress=request.GET.get("progress", ""),
    )
    queryset = selectors.tasks_for(
        _projects(request),
        owner=filters.owner,
        status=filters.status,
        priority=filters.priority,
        due=filters.due,
        progress=filters.progress,
    )

    return render(
        request,
        "pages/task_list.html",
        {"board": build_task_board(queryset, filters), "page_title": "タスク一覧"},
    )


@login_required
def progress(request: HttpRequest) -> HttpResponse:
    projects = _projects(request)
    report = build_progress_report(
        projects,
        selectors.delay_candidate_tasks_for(projects),
        selectors.blocked_tasks_for(projects),
    )

    return render(
        request,
        "pages/progress.html",
        {"report": report, "page_title": "進捗予測・介入"},
    )


@login_required
def quality(request: HttpRequest) -> HttpResponse:
    projects = _projects(request)
    report = build_quality_report(
        selectors.quality_metrics_for(projects),
        selectors.defects_for(projects),
    )

    return render(
        request,
        "pages/quality.html",
        {"report": report, "page_title": "品質リアルタイム管理"},
    )


@login_required
def risk(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    report = build_risk_report(selectors.risks_for(_projects(request), status=status))

    return render(
        request,
        "pages/risk_list.html",
        {"report": report, "status": status, "page_title": "リスク予測・対策"},
    )


@login_required
def change(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    report = build_change_report(
        selectors.change_requests_for(_projects(request), status=status)
    )

    return render(
        request,
        "pages/change_list.html",
        {"report": report, "status": status, "page_title": "変更影響分析"},
    )


@login_required
def intervention(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    report = build_intervention_report(
        selectors.interventions_for(_projects(request), status=status)
    )

    return render(
        request,
        "pages/intervention_list.html",
        {"report": report, "status": status, "page_title": "AI介入提案"},
    )


@login_required
def kpi(request: HttpRequest) -> HttpResponse:
    """KPI・効果測定。

    実測値が1件も無いと画面が空になり、導入前後の比較ができない。
    そのときだけ WBS・課題・不具合から算出した代替指標を出す。
    実測と混同させないため、テンプレート側で「実測ではない」ことを明示する。
    """

    projects = _projects(request)
    report = build_kpi_report(selectors.kpi_measurements_for(projects))

    return render(
        request,
        "pages/kpi.html",
        {
            "report": report,
            "derived_rows": build_derived_rows(projects) if not report.rows else (),
            "page_title": "KPI・効果測定",
        },
    )


@login_required
def intervention_decide(request: HttpRequest, pk) -> HttpResponse:
    """AI 介入提案に人の判断を記録する。

    対象は必ず「参照できる案件に紐づく提案」に絞る。テナントを越えた ID を
    直接叩かれても 404 になるよう、取得の時点で候補を限定している。
    """

    proposal = get_object_or_404(
        InterventionProposal.objects.select_related("project", "project__tenant", "alert"),
        pk=pk,
        project__in=_projects(request),
    )

    if not is_pending(proposal):
        messages.warning(request, "この提案はすでに判断済みです。履歴を保つため再判断はできません。")

        return redirect("dashboard:intervention")

    form = InterventionDecisionForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            decided = decide_intervention(
                proposal,
                user=request.user,
                status=form.cleaned_data["status"],
                decision_reason=form.cleaned_data["decision_reason"],
                modified_action=form.cleaned_data["modified_action"],
            )
        except AlreadyDecidedError:
            messages.warning(request, "他の利用者が先に判断しました。最新の状態を確認してください。")
        else:
            messages.success(
                request,
                f"「{decided.title}」を{decided.get_status_display()}として記録しました。",
            )

        return redirect("dashboard:intervention")

    return render(
        request,
        "pages/intervention_form.html",
        {
            "form": form,
            "proposal": proposal,
            "page_title": "AI介入提案の判断",
        },
    )
