"""共通画面と、認証・権限・通信経路で失敗したときの共通画面。"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
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

    ナビゲーション定義（`apps.core.navigation`）が管理者限定と宣言しているため、
    ビュー側でも同じ条件を強制する。宣言だけでビューが素通しだと、
    メニューに出ない画面へ URL 直打ちで入れてしまい、権限境界が形だけになる。
    """

    if not request.user.is_tenant_admin:
        raise PermissionDenied("設定の閲覧はテナント管理者のみ行えます")

    from apps.core.services.ai_settings import masked_ai_settings

    return render(
        request,
        "pages/settings.html",
        {"ai_settings": masked_ai_settings(), "page_title": "AI設定"},
    )


@login_required
def screen_map(request: HttpRequest) -> HttpResponse:
    """画面と移植状況の一覧。"""

    return render(
        request,
        "pages/screen_map.html",
        {"items": all_items(), "page_title": "画面マップ"},
    )


def bad_request(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "400.html", status=400)


def permission_denied(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "403.html", status=403)


def page_not_found(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "404.html", status=404)


def server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "500.html", status=500)


@login_required
def not_implemented(request: HttpRequest) -> HttpResponse:
    """まだ実装していない画面の着地先。

    ナビゲーションで `status="planned"` の項目は、押しても何も起きないと
    「壊れている」と受け取られる。ここへ着地させ、戻り先と次の操作を出す。

    参照するビューが無いままテンプレートだけ置いておくと、レンダリングされない
    ＝壊れていても気づけない。URL を与えて、他画面と同じ検証の対象にする。
    """

    return render(
        request,
        "pages/not_implemented.html",
        {
            "page_title": "この画面はまだありません",
            "requested_label": request.GET.get("screen", "").strip()[:60],
            "return_to": request.GET.get("next") or "/",
        },
    )
