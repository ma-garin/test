"""共通画面。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.navigation import all_items


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """死活監視用。認証不要、DB へも触れない。"""

    return JsonResponse({"status": "ok"})


@login_required
def settings_page(request: HttpRequest) -> HttpResponse:
    """AI プロバイダ等の設定画面。

    値の表示は必ずマスク済みのものを使う（`apps.core.services.ai_settings`）。
    生の API キーをテンプレートへ渡してはいけない。
    """

    from apps.core.services.ai_settings import masked_ai_settings

    return render(
        request,
        "pages/settings.html",
        {"ai_settings": masked_ai_settings(), "page_title": "設定"},
    )


@login_required
def screen_map(request: HttpRequest) -> HttpResponse:
    """画面と移植状況の一覧。ドキュメントとコードのずれを見つけるための画面。"""

    return render(
        request,
        "pages/screen_map.html",
        {"items": all_items(), "page_title": "画面マップ"},
    )
