"""テンプレート共通のナビゲーション情報。"""

from __future__ import annotations

from django.http import HttpRequest

from apps.core.navigation import navigation_for


def navigation(request: HttpRequest) -> dict:
    return {
        "nav_sections": navigation_for(getattr(request, "user", None)),
        "current_tenant": getattr(request, "tenant", None),
    }
