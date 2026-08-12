"""テンプレート共通のナビゲーション情報。"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils import timezone

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

    tenant = getattr(request, "tenant", None)
    project = getattr(request, "project", None)
    if project is not None:
        scope_label = f"{tenant.name if tenant else 'テナント未選択'} ／ {project.code} {project.name}"
    elif tenant is not None:
        scope_label = f"{tenant.name} ／ 全案件"
    else:
        scope_label = "対象テナントを選択してください"

    return {
        "nav_sections": navigation_for(getattr(request, "user", None), current),
        "current_tenant": tenant,
        # 選択中の案件。全画面のヘッダーに出し、いま何を見ているかを常に示す。
        "current_project": project,
        "current_url_name": current,
        # データそのものの更新日時ではなく、画面の集計・表示を確認した時点。
        # これを区別して出すことで、画面をいつ見直したかを利用者が判断できる。
        "page_rendered_at": timezone.localtime(),
        "page_scope_label": scope_label,
    }
