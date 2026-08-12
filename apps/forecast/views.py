"""LDF-04: ライブ着地予測の画面。

計算は `services.engine`、並べ替えは `services.board`、機能単位の組み立ては
`services.feature_view` に置き、ここは「参照できる案件へ絞って渡す」だけにする。
テナント・案件の分離は `scoped_projects_for()` を必ず通すことで担保する。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.forecast.models.snapshots import ForecastReview, ForecastSnapshot
from apps.forecast.services.board import build_forecast_board
from apps.forecast.services.feature_view import build_feature_detail, latest_snapshots_for
from apps.forecast.services.report import DAILY_WINDOW, WEEKLY_WINDOW, build_report
from apps.forecast.services.review import ReviewError, record_review, review_link
from apps.graph.models.graph import Feature, WorkLink
from apps.projects.selectors import scoped_projects_for


@login_required
def live_forecast(request: HttpRequest) -> HttpResponse:
    """案件の 2日後・1週間後・最終期日の着地を、危険な順に出す。"""

    projects = scoped_projects_for(request)
    board = build_forecast_board(projects, timezone.localdate())
    return render(
        request,
        "pages/live_forecast.html",
        {
            "board": board,
            "page_title": "ライブ着地予測",
            "features": Feature.objects.filter(project__in=projects).select_related("project"),
        },
    )


@login_required
def feature_detail(request: HttpRequest, pk) -> HttpResponse:
    """機能 1 件の現在地・根拠・3 時点の着地。"""

    feature = get_object_or_404(
        Feature.objects.filter(project__in=scoped_projects_for(request)).select_related(
            "project"
        ),
        pk=pk,
    )
    detail = build_feature_detail(feature, timezone.localdate())
    return render(
        request,
        "pages/feature_detail.html",
        {
            "detail": detail,
            "feature": feature,
            "snapshots": latest_snapshots_for(feature),
            "page_title": feature.name,
            "return_to": request.GET.get("next") or "/forecast/",
        },
    )


@login_required
def daily_report(request: HttpRequest) -> HttpResponse:
    """LDF-08: 前回確認後の差分・判断待ち・未確認事項を集めた報告の下書き。

    ここで作るのは下書きまでで、外部へは送信しない。
    """

    projects = list(scoped_projects_for(request))
    window = WEEKLY_WINDOW if request.GET.get("range") == "weekly" else DAILY_WINDOW
    drafts = [build_report(project, window=window) for project in projects]

    return render(
        request,
        "pages/forecast_report.html",
        {
            "drafts": drafts,
            "is_weekly": window == WEEKLY_WINDOW,
            "page_title": "日次・週次報告",
            "notification_total": sum(len(draft.notifications) for draft in drafts),
        },
    )


@login_required
@require_POST
def review_snapshot(request: HttpRequest, pk) -> HttpResponse:
    """AH-07: 予測への人の判断を記録する。外部システムへは書き込まない。"""

    snapshot = get_object_or_404(
        ForecastSnapshot.objects.filter(project__in=scoped_projects_for(request)), pk=pk
    )
    decision = request.POST.get("decision", "")
    if decision not in ForecastReview.Decision.values:
        messages.error(request, "判断の値が不正です。")
        return redirect(request.POST.get("next") or "forecast:live")

    try:
        record_review(
            snapshot,
            request.user,
            decision=decision,
            reason=request.POST.get("reason", "").strip(),
            corrected_date=request.POST.get("corrected_date") or None,
        )
    except (ReviewError, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "予測への判断を記録しました。外部システムへは反映していません。")

    return redirect(request.POST.get("next") or "forecast:live")


@login_required
@require_POST
def review_work_link(request: HttpRequest, pk) -> HttpResponse:
    """AH-07: 関連候補の確定・否定。確認者と理由を必ず残す。"""

    link = get_object_or_404(
        WorkLink.objects.filter(project__in=scoped_projects_for(request)), pk=pk
    )
    confirm = request.POST.get("action") == "confirm"
    review_link(link, request.user, confirm=confirm, reason=request.POST.get("reason", ""))
    messages.success(
        request,
        "関連を確定しました。予測の根拠に使われます。"
        if confirm
        else "関連を否定しました。予測の根拠には使いません。",
    )
    return redirect(request.POST.get("next") or "forecast:live")
