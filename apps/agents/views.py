"""Agentic トレースの閲覧（REQ-AG-009）。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.agents.models import AgentRun


def _runs_for(request: HttpRequest):
    queryset = AgentRun.objects.select_related("project", "user", "evidence")

    if request.user.is_superuser and request.tenant is None:
        return queryset

    return queryset.filter(tenant=request.tenant or request.user.tenant)


@login_required
def run_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/agent_run_list.html",
        {"runs": _runs_for(request)[:100], "page_title": "Agenticトレース"},
    )


@login_required
def run_detail(request: HttpRequest, pk) -> HttpResponse:
    run = get_object_or_404(_runs_for(request).prefetch_related("steps"), pk=pk)

    return render(
        request,
        "pages/agent_run_detail.html",
        {"run": run, "page_title": "実行トレース"},
    )
