"""管制ダッシュボード。

P0 の受入条件は「選択中案件の集計値とアラートが表示され、詳細画面へ遷移できる」。
未実装の画面は placeholder で 200 を返し、ナビゲーションが壊れないようにしている。
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.dashboard.services.overview import build_overview
from apps.projects.selectors import projects_for


@login_required
def control(request: HttpRequest) -> HttpResponse:
    projects = projects_for(request.user, request.tenant)

    return render(
        request,
        "pages/control_dashboard.html",
        {"overview": build_overview(projects), "page_title": "管制ダッシュボード"},
    )


def _placeholder(title: str):
    """未実装画面の共通ハンドラ。

    404 ではなく「未実装」と明示した 200 を返す。ナビゲーションから飛べる画面が
    エラーになると、移植の進捗と不具合の区別がつかなくなるため。
    """

    @login_required
    def view(request: HttpRequest) -> HttpResponse:
        return render(request, "pages/not_implemented.html", {"page_title": title})

    return view


tasks = _placeholder("タスク一覧")
progress = _placeholder("進捗予測・介入")
quality = _placeholder("品質リアルタイム管理")
risk = _placeholder("リスク予測・対策")
change = _placeholder("変更影響分析")
intervention = _placeholder("AI介入提案")
kpi = _placeholder("KPI・効果測定")
