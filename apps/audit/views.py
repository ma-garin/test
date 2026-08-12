"""操作ログ・フィードバックの閲覧。

監査画面の用途は「あとから追える」ことなので、既定では期間で隠さず、
絞り込みは GET で明示する（URL を共有すれば同じ抽出を再現できる）。
表示するのは保存時にマスク済みの本文だけで、ここで生値へ戻す処理は持たない。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import BooleanField, Count, ExpressionWrapper, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.audit.forms import FeedbackForm
from apps.audit.models import Feedback, OperationLog
from apps.audit.selectors import feedbacks_for, operation_logs_for
from apps.audit.services import feedback_stats
from apps.audit.services.feedback_submit import submit_feedback
from apps.core.pagination import page_window, paginate, query_without_page

#: 操作ログの期間候補。既定は全期間（0）。監査は遡れることが用途なので既定で隠さない。
OPERATION_PERIOD_CHOICES: tuple[tuple[int, str], ...] = (
    (0, "全期間"),
    (7, "直近7日"),
    (30, "直近30日"),
    (90, "直近90日"),
)

#: 成否の絞り込み。真偽値を直接クエリで受けず、語彙で受ける。
RESULT_CHOICES: tuple[tuple[str, str], ...] = (("ok", "成功のみ"), ("ng", "失敗のみ"))

#: フィードバック明細の並び。既定は「直すべき順」。
FEEDBACK_SORT_CHOICES: tuple[tuple[str, str], ...] = (
    ("priority", "優先順（事実誤認→低評価→新着）"),
    ("new", "新着順"),
)

#: 事実誤認のクイックフィルタ。
FACT_CHOICES: tuple[tuple[str, str], ...] = (
    ("error", "事実誤認ありのみ"),
    ("ok", "事実誤認なしのみ"),
)

#: 再現情報の有無。対象（回答・実行）が特定でき、かつ本文が書かれていること。
#: この 2 つが無いと、指摘を受けても再現できず直しようがない。
_HAS_REPRO = ~Q(comment="") & (Q(answer__isnull=False) | Q(agent_run__isnull=False))


def _page_context(page, request: HttpRequest) -> dict:
    """ページャ用のコンテキスト。絞り込み条件を保ったままページを送れるようにする。"""

    return {
        "page": page,
        "page_window": page_window(page),
        "page_query": query_without_page(request),
    }


def _actor_options(queryset: QuerySet) -> list[dict]:
    """絞り込み用の実施者一覧。実際に記録がある人だけを出す。"""

    rows = (
        queryset.exclude(user__isnull=True)
        .values("user_id", "user__username")
        .annotate(count=Count("pk"))
        .order_by("-count", "user__username")
    )

    return [{"id": row["user_id"], "label": row["user__username"], "count": row["count"]} for row in rows]


def _parse_user_id(raw) -> uuid.UUID | None:
    """利用者 ID を検証する。主キーは UUID なので、不正値は「指定なし」へ倒す。

    そのままクエリへ渡すと `ValidationError` で 500 になる。URL を手で
    編集した程度で監査画面が落ちないようにここで止める。
    """

    value = str(raw or "").strip()

    try:
        return uuid.UUID(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class OperationFilters:
    """操作ログの絞り込み条件。"""

    days: int = 0
    user_id: uuid.UUID | None = None
    action: str = ""
    target: str = ""
    result: str = ""

    @property
    def is_active(self) -> bool:
        return bool(self.days or self.user_id or self.action or self.target or self.result)

    @property
    def period_label(self) -> str:
        return dict(OPERATION_PERIOD_CHOICES).get(self.days, "全期間")

    def labels(self, actor_label: str = "") -> list[str]:
        parts: list[str] = []

        if self.days:
            parts.append(f"期間: {self.period_label}")

        if self.user_id and actor_label:
            parts.append(f"操作者: {actor_label}")

        if self.action:
            parts.append(f"操作種別: {self.action}")

        if self.target:
            parts.append(f"対象: {self.target}")

        if self.result:
            parts.append(dict(RESULT_CHOICES)[self.result])

        return parts


def parse_operation_filters(params) -> OperationFilters:
    """クエリ文字列を条件へ変換する。不正値は既定へ倒し、500 にしない。"""

    allowed_days = {days for days, _ in OPERATION_PERIOD_CHOICES}
    raw_days = str(params.get("period", "")).strip()
    days = int(raw_days) if raw_days.isdigit() and int(raw_days) in allowed_days else 0

    result = str(params.get("result", "")).strip()

    return OperationFilters(
        days=days,
        user_id=_parse_user_id(params.get("user", "")),
        action=str(params.get("action", "")).strip()[:120],
        target=str(params.get("target", "")).strip()[:120],
        result=result if result in dict(RESULT_CHOICES) else "",
    )


def apply_operation_filters(
    queryset: QuerySet[OperationLog], filters: OperationFilters
) -> QuerySet[OperationLog]:
    """テナント分離済みのクエリへ、表示条件だけを重ねる。"""

    filtered = queryset

    if filters.days:
        filtered = filtered.filter(created_at__gte=timezone.now() - timedelta(days=filters.days))

    if filters.user_id is not None:
        filtered = filtered.filter(user_id=filters.user_id)

    if filters.action:
        filtered = filtered.filter(action=filters.action)

    if filters.target:
        filtered = filtered.filter(target__icontains=filters.target)

    if filters.result:
        filtered = filtered.filter(succeeded=filters.result == "ok")

    return filtered


@login_required
def operation_list(request: HttpRequest) -> HttpResponse:
    """操作ログ。監査で遡れなければ意味がないので、先頭打ち切りではなく全件を辿らせる。"""

    scoped = operation_logs_for(request.user, request.tenant)
    filters = parse_operation_filters(request.GET)
    page = paginate(apply_operation_filters(scoped, filters), request)
    actors = _actor_options(scoped)
    actor_label = next((a["label"] for a in actors if a["id"] == filters.user_id), "")

    return render(
        request,
        "pages/operation_list.html",
        {
            "logs": page.object_list,
            **_page_context(page, request),
            "filters": filters,
            "filter_labels": filters.labels(actor_label),
            "period_choices": OPERATION_PERIOD_CHOICES,
            "result_choices": RESULT_CHOICES,
            "actor_options": actors,
            "action_options": sorted(set(scoped.values_list("action", flat=True).distinct()[:200])),
            "page_title": "操作ログ",
        },
    )


@dataclass(frozen=True)
class FeedbackViewFilters:
    """明細側の絞り込み。集計（期間・利用者）とは分けて持つ。"""

    fact: str = ""
    rating: int | None = None
    sort: str = "priority"

    @property
    def is_active(self) -> bool:
        return bool(self.fact or self.rating or self.sort != "priority")

    @property
    def labels(self) -> list[str]:
        parts: list[str] = []

        if self.fact:
            parts.append(dict(FACT_CHOICES)[self.fact])

        if self.rating:
            parts.append(f"評価: {Feedback.Rating(self.rating).label}")

        parts.append(dict(FEEDBACK_SORT_CHOICES)[self.sort])

        return parts


def parse_feedback_filters(params) -> FeedbackViewFilters:
    fact = str(params.get("fact", "")).strip()
    raw_rating = str(params.get("rating", "")).strip()
    rating = int(raw_rating) if raw_rating.isdigit() and int(raw_rating) in Feedback.Rating.values else None
    sort = str(params.get("sort", "")).strip()

    return FeedbackViewFilters(
        fact=fact if fact in dict(FACT_CHOICES) else "",
        rating=rating,
        sort=sort if sort in dict(FEEDBACK_SORT_CHOICES) else "priority",
    )


def apply_feedback_filters(
    queryset: QuerySet[Feedback], filters: FeedbackViewFilters
) -> QuerySet[Feedback]:
    """明細の抽出と並び。優先順は「事実誤認 → 低評価 → 新着」。"""

    filtered = queryset

    if filters.fact:
        filtered = filtered.filter(has_fact_error=filters.fact == "error")

    if filters.rating:
        filtered = filtered.filter(rating=filters.rating)

    priority_order = ("-has_fact_error", "rating", "-created_at")
    ordering = priority_order if filters.sort == "priority" else ("-created_at",)

    return (
        filtered.annotate(has_repro=ExpressionWrapper(_HAS_REPRO, output_field=BooleanField()))
        .select_related("agent_run")
        .prefetch_related("agent_run__reviews")
        .order_by(*ordering)
    )


def feedback_quick_views(filters: FeedbackViewFilters) -> list[dict]:
    """直すべきフィードバックへ 1 クリックで到達する導線。"""

    return [
        {"label": "事実誤認あり", "query": "fact=error", "is_active": filters.fact == "error"},
        {
            "label": "役に立たなかった",
            "query": f"rating={Feedback.Rating.BAD}",
            "is_active": filters.rating == Feedback.Rating.BAD,
        },
    ]


@login_required
def feedback_list(request: HttpRequest) -> HttpResponse:
    """評価分布と事実誤認件数を、期間・利用者で絞り込んで見せる。"""

    scoped = feedbacks_for(request.user, request.tenant)
    criteria = feedback_stats.parse_criteria(request.GET)
    filtered = feedback_stats.apply_criteria(scoped, criteria)
    view_filters = parse_feedback_filters(request.GET)
    page = paginate(apply_feedback_filters(filtered, view_filters), request)

    return render(
        request,
        "pages/feedback_list.html",
        {
            "feedbacks": page.object_list,
            **_page_context(page, request),
            # 集計は期間・利用者の条件だけで取る。クイックフィルタまで反映すると
            #「事実誤認ありのみ」で事実誤認率 100% になり、受入条件の判定に使えない。
            "stats": feedback_stats.summarize(filtered),
            "criteria": criteria,
            "period_choices": feedback_stats.PERIOD_CHOICES,
            "reporter_options": feedback_stats.reporter_options(scoped),
            "view_filters": view_filters,
            "quick_views": feedback_quick_views(view_filters),
            "fact_choices": FACT_CHOICES,
            "rating_choices": Feedback.Rating.choices,
            "sort_choices": FEEDBACK_SORT_CHOICES,
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
