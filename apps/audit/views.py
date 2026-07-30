"""操作ログ・フィードバックの閲覧。"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.audit.forms import FeedbackForm
from apps.audit.selectors import feedbacks_for, operation_logs_for
from apps.audit.services import feedback_stats
from apps.audit.services.feedback_submit import submit_feedback

#: 一覧に出す最大件数。集計は全件、明細は先頭だけという役割分担にしている。
LIST_LIMIT = 200


@login_required
def operation_list(request: HttpRequest) -> HttpResponse:
    logs = operation_logs_for(request.user, request.tenant)

    return render(
        request,
        "pages/operation_list.html",
        {"logs": logs[:LIST_LIMIT], "page_title": "操作ログ"},
    )


@login_required
def feedback_list(request: HttpRequest) -> HttpResponse:
    """評価分布と事実誤認件数を、期間・利用者で絞り込んで見せる。"""

    scoped = feedbacks_for(request.user, request.tenant)
    criteria = feedback_stats.parse_criteria(request.GET)
    filtered = feedback_stats.apply_criteria(scoped, criteria)

    return render(
        request,
        "pages/feedback_list.html",
        {
            "feedbacks": filtered[:LIST_LIMIT],
            "stats": feedback_stats.summarize(filtered),
            "criteria": criteria,
            "period_choices": feedback_stats.PERIOD_CHOICES,
            "reporter_options": feedback_stats.reporter_options(scoped),
            "page_title": "フィードバック",
        },
    )


@login_required
def feedback_create(request: HttpRequest) -> HttpResponse:
    """AI の回答に対するフィードバックを投稿する。

    対象の選択肢は自テナント分だけに絞る（`FeedbackForm` 側）。テナントが
    確定していない利用者は投稿させず、一覧へ戻す。
    """

    tenant = getattr(request, "tenant", None) or getattr(request.user, "tenant", None)

    if tenant is None:
        messages.error(request, "テナントが選択されていないため、フィードバックを投稿できません。")

        return redirect("audit:feedback_list")

    form = FeedbackForm(request.POST or None, tenant=tenant)

    if request.method == "POST" and form.is_valid():
        submit_feedback(
            tenant=tenant,
            user=request.user,
            rating=form.cleaned_data["rating"],
            comment=form.cleaned_data["comment"],
            has_fact_error=form.cleaned_data["has_fact_error"],
            answer=form.cleaned_data["answer"],
            agent_run=form.cleaned_data["agent_run"],
        )
        messages.success(request, "フィードバックを登録しました。集計に反映されます。")

        return redirect("audit:feedback_list")

    return render(
        request,
        "pages/feedback_form.html",
        {"form": form, "page_title": "フィードバック投稿"},
    )
