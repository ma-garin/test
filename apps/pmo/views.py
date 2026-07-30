"""PMO 支援画面。"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render

from apps.agents.models import AgentRun
from apps.agents.services import orchestrator
from apps.core.pagination import page_window, paginate, query_without_page
from apps.pmo import selectors
from apps.pmo.models import Deliverable
from apps.pmo.services import approval as approval_service
from apps.pmo.services import deliverables as deliverable_service
from apps.pmo.services import prompt_library as prompt_library_service
from apps.rag.models import VectorIndex


@login_required
def consultation(request: HttpRequest) -> HttpResponse:
    """PMO 相談。オーケストレーターを通し、意図・計画・根拠評価を画面へ返す。"""

    question = request.GET.get("q", "").strip()
    result = None

    if question and request.tenant:
        index = VectorIndex.objects.filter(tenant=request.tenant, project__isnull=True).first()
        result = orchestrator.run(
            tenant=request.tenant,
            question=question,
            area=AgentRun.Area.PMO_CONSULTATION,
            index=index,
            user=request.user,
        )

    return render(
        request,
        "pages/pmo_consultation.html",
        {"question": question, "result": result, "page_title": "PMO相談・状況整理"},
    )


@login_required
def planning(request: HttpRequest) -> HttpResponse:
    """計画策定。ドラフト一覧と、選択した 1 件のレビュー観点を出す。"""

    drafts = selectors.plan_drafts_for(request.user, request.tenant)
    page = paginate(drafts, request)
    # 選択中の 1 件は全件から解決する。2 ページ目の行を開いたときに
    # 詳細だけ先頭の計画へ化ける、という壊れ方を防ぐため。
    all_drafts = list(drafts)

    return render(
        request,
        "pages/pmo_planning.html",
        {
            "drafts": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "selected": _pick(all_drafts, request.GET.get("draft")),
            "page_title": "計画策定",
        },
    )


@login_required
def deliverables(request: HttpRequest) -> HttpResponse:
    """成果物支援。AI 生成本文と確定本文を並べ、赤字率を示す。"""

    # 集計（件数・平均赤字率）は全件から出す。ページを送るたびに KPI が動くと、
    # その数字が現状を表していないことになるため。
    report = deliverable_service.build_report(
        selectors.deliverables_for(request.user, request.tenant)
    )
    page = paginate(report.rows, request)
    selected = _pick(report.rows, request.GET.get("deliverable"), key=_row_pk)

    return render(
        request,
        "pages/pmo_deliverables.html",
        {
            "report": report,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "selected": selected,
            "target_percent": deliverable_service.CORRECTION_RATE_TARGET_PERCENT,
            "page_title": "成果物支援",
        },
    )


@login_required
def approvals(request: HttpRequest) -> HttpResponse:
    """報告生成・承認。承認は POST で受け、判断とその履歴を残す。"""

    if request.method == "POST":
        return _decide(request)

    # 判断待ち件数・ブロック件数は全件から数える。ページ送りで警告の件数が
    # 減って見えると、対応漏れの原因になる。
    report = deliverable_service.build_report(
        selectors.deliverables_awaiting_decision_for(request.user, request.tenant)
    )
    page = paginate(report.rows, request)

    return render(
        request,
        "pages/pmo_approvals.html",
        {
            "report": report,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "history": selectors.approvals_for(request.user, request.tenant)[:20],
            "page_title": "報告生成・承認",
        },
    )


def _decide(request: HttpRequest) -> HttpResponseRedirect:
    """承認画面の POST。テナント外の成果物は 404 にして触らせない。"""

    deliverable = get_object_or_404(
        selectors.deliverables_for(request.user, request.tenant),
        pk=request.POST.get("deliverable"),
    )
    result = approval_service.decide(
        deliverable=deliverable,
        actor=request.user,
        decision=request.POST.get("decision", ""),
        comment=request.POST.get("comment", "").strip(),
    )

    if result.ok:
        messages.success(request, result.message)
    else:
        messages.error(request, result.message)

    return redirect("pmo:approvals")


@login_required
def prompt_library(request: HttpRequest) -> HttpResponse:
    """プロンプトライブラリ。相談画面へ本文を渡すリンクを持つ。"""

    entries = prompt_library_service.entries_for(request.tenant)

    return render(
        request,
        "pages/pmo_prompt_library.html",
        {
            "entries": entries,
            "categories": prompt_library_service.categories(entries),
            "page_title": "プロンプトライブラリ",
        },
    )


@login_required
def education(request: HttpRequest) -> HttpResponse:
    """教育支援。新任 PMO 向けの操作導線と用語解説。"""

    return render(
        request,
        "pages/pmo_education.html",
        {"deliverable_kinds": Deliverable.Kind.choices, "page_title": "教育支援"},
    )


def _row_pk(row):
    return row.deliverable.pk


def _pick(items: list, raw_pk: str | None, key=lambda item: item.pk):
    """一覧から選択中の 1 件を返す。指定が無ければ先頭。

    pk は UUID なので数値として解釈せず、文字列のまま突き合わせる。
    URL に不正な pk が来ても画面を落とさないため、例外にせず先頭へ倒す。
    """

    if not items:
        return None

    if raw_pk:
        return next((item for item in items if str(key(item)) == raw_pk), items[0])

    return items[0]
