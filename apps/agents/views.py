"""Agentic トレースの閲覧（REQ-AG-009）。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.agents.models import AgentRun
from apps.core.pagination import page_window, paginate, query_without_page
from apps.projects.selectors import projects_for


def _runs_for(request: HttpRequest):
    """閲覧できる実行履歴。

    トレースには相談の本文と根拠がそのまま残る。テナントで絞るだけでは、
    案件のメンバーでない人が他案件の相談内容まで読めてしまう。案件配下の
    実行は `projects_for()`（＝案件メンバーの範囲）に揃える。
    案件に紐づかない実行は本人のものだけを見せる。テナント管理者は運用の
    ため全件を見られる（`permissions` の判定順と同じ扱い）。
    """

    queryset = AgentRun.objects.select_related("project", "user", "evidence")

    if request.user.is_superuser and request.tenant is None:
        return queryset

    queryset = queryset.filter(tenant=request.tenant or request.user.tenant)

    if request.user.is_tenant_admin:
        return queryset

    return queryset.filter(
        Q(project__in=projects_for(request.user, request.tenant))
        | Q(project__isnull=True, user=request.user)
    )


@login_required
def run_list(request: HttpRequest) -> HttpResponse:
    """実行履歴。先頭 100 件の打ち切りでは古いトレースへ辿り着けないため、ページで送る。"""

    page = paginate(_runs_for(request), request)

    return render(
        request,
        "pages/agent_run_list.html",
        {
            "runs": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "page_title": "Agenticトレース",
        },
    )


@login_required
def run_detail(request: HttpRequest, pk) -> HttpResponse:
    run = get_object_or_404(_runs_for(request).prefetch_related("steps"), pk=pk)

    return render(
        request,
        "pages/agent_run_detail.html",
        {"run": run, "page_title": "実行トレース"},
    )
