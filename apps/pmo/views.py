"""PMO 支援画面。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.agents.models import AgentRun
from apps.agents.services import orchestrator
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


def _placeholder(title: str):
    @login_required
    def view(request: HttpRequest) -> HttpResponse:
        return render(request, "pages/not_implemented.html", {"page_title": title})

    return view


planning = _placeholder("計画策定")
deliverables = _placeholder("成果物支援")
approvals = _placeholder("報告生成・承認")
prompt_library = _placeholder("プロンプトライブラリ")
education = _placeholder("教育支援")
