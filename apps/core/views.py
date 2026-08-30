"""共通画面。"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """死活監視用。認証不要、DB へも触れない。"""

    return JsonResponse({"status": "ok"})
