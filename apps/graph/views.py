"""GE-04 / GE-05: 影響範囲ビューと、グラフ品質ダッシュボード。

影響範囲は「確定・候補・否定・未確認」を混ぜない。PMO が候補を確定情報と
誤認したまま報告へ載せることが、この製品で最も避けたい失敗である。
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.graph.services.quality import build_quality_report
from apps.graph.services.queries import build_impact
from apps.projects.models import Defect
from apps.projects.selectors import scoped_projects_for


@login_required
def impact_view(request: HttpRequest, pk) -> HttpResponse:
    """不具合を起点に、機能・技術要素・WBS・マイルストーンへの経路を出す。"""

    defect = get_object_or_404(
        Defect.objects.filter(project__in=scoped_projects_for(request)).select_related(
            "project"
        ),
        pk=pk,
    )
    impact = build_impact(defect)
    confirmed_only = build_impact(defect, include_candidates=False)

    return render(
        request,
        "pages/impact_view.html",
        {
            "defect": defect,
            "impact": impact,
            "confirmed_node_count": len(confirmed_only.nodes),
            "page_title": f"影響範囲: {defect.title}",
            "return_to": request.GET.get("next") or "/projects/defects/",
        },
    )


@login_required
def graph_quality(request: HttpRequest) -> HttpResponse:
    """予測が弱い理由を、データ整備の作業として出す。"""

    projects = list(scoped_projects_for(request))
    reports = [build_quality_report(project) for project in projects]

    return render(
        request,
        "pages/graph_quality.html",
        {
            "reports": reports,
            "page_title": "グラフ品質・データ整備",
            "repair_total": sum(len(report.repairs) for report in reports),
            "cycle_total": sum(len(report.cycles) for report in reports),
        },
    )
