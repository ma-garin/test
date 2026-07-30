"""操作ログ・フィードバックの閲覧。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.audit.models import Feedback, OperationLog


@login_required
def operation_list(request: HttpRequest) -> HttpResponse:
    logs = OperationLog.objects.filter(tenant=request.tenant or request.user.tenant)

    return render(
        request,
        "pages/operation_list.html",
        {"logs": logs[:200], "page_title": "操作ログ"},
    )


@login_required
def feedback_list(request: HttpRequest) -> HttpResponse:
    feedbacks = Feedback.objects.filter(tenant=request.tenant or request.user.tenant)

    return render(
        request,
        "pages/feedback_list.html",
        {"feedbacks": feedbacks[:200], "page_title": "フィードバック"},
    )
