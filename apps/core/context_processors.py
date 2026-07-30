"""テンプレート共通のナビゲーション情報。"""

from __future__ import annotations

from django.http import HttpRequest

from apps.core.navigation import navigation_for


def navigation(request: HttpRequest) -> dict:
    """サイドバーの描画と active 判定に必要な情報を渡す。

    `current_url_name` は `app_name:url_name` 形式。`NavItem.url_name` と
    そのまま比較できるので、テンプレート側に判定ロジックを書かずに済む。
    """

    match = getattr(request, "resolver_match", None)
    current = ""

    if match is not None:
        current = f"{match.app_name}:{match.url_name}" if match.app_name else (match.url_name or "")

    return {
        "nav_sections": navigation_for(getattr(request, "user", None)),
        "current_tenant": getattr(request, "tenant", None),
        "current_url_name": current,
    }
