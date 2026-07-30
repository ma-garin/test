"""案件一覧・詳細。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.projects.selectors import projects_for


@login_required
def project_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/project_list.html",
        {"projects": projects_for(request.user, request.tenant), "page_title": "案件管理"},
    )


@login_required
def project_detail(request: HttpRequest, pk) -> HttpResponse:
    project = get_object_or_404(projects_for(request.user, request.tenant), pk=pk)

    return render(
        request,
        "pages/project_detail.html",
        {"project": project, "page_title": project.name},
    )
