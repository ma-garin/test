"""現在のテナントをリクエストへ紐づけるミドルウェア。"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

TENANT_SESSION_KEY = "current_tenant_id"


class CurrentTenantMiddleware:
    """`request.tenant` を解決する。

    解決順序は以下。

    1. セッションで明示的に選択されたテナント（テナント切替 UI）
    2. ログインユーザーの所属テナント
    3. 未認証なら None
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.tenant = self._resolve_tenant(request)

        return self.get_response(request)

    def _resolve_tenant(self, request: HttpRequest):
        user = getattr(request, "user", None)

        if user is None or not user.is_authenticated:
            return None

        from apps.accounts.models import Tenant

        selected_id = request.session.get(TENANT_SESSION_KEY)

        if selected_id:
            tenant = Tenant.objects.filter(pk=selected_id, is_active=True).first()

            # 選べるのは自分の所属テナントだけ。スーパーユーザーのみ横断できる。
            if tenant and (user.is_superuser or tenant.pk == user.tenant_id):
                return tenant

        return user.tenant
