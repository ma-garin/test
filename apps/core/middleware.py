"""現在のテナントと案件をリクエストへ紐づけるミドルウェア。"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

TENANT_SESSION_KEY = "current_tenant_id"
PROJECT_SESSION_KEY = "current_project_id"


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
        from apps.core.services import ai_settings

        request.tenant = self._resolve_tenant(request)
        request.project = self._resolve_project(request)

        # AI 設定は利用者ごとに違う。サービス層すべてへ user を引き回すと、
        # 管理コマンド経由の呼び出しまで引数が増えて壊れやすくなるため、
        # リクエストの間だけ文脈として持たせる。必ず reset して次の
        # リクエスト（同一スレッドの使い回し）へ漏らさない。
        token = ai_settings.set_current_user(
            request.user if getattr(request, "user", None) is not None else None
        )

        try:
            return self.get_response(request)
        finally:
            ai_settings.reset_current_user(token)

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

    def _resolve_project(self, request: HttpRequest):
        """選択中の案件。未選択なら None（＝テナント内の全案件を見る）。

        PMO は複数案件を担当するので、選択は「絞り込み」であって必須ではない。
        全体を俯瞰する画面（管制ダッシュボード）と、1案件を掘る画面の
        どちらも同じ導線で使えるようにしている。

        選択済みの案件が参照できなくなった場合（メンバーから外れた、削除された、
        テナントを切り替えた）は、黙って None へ戻す。存在しない案件で
        絞り込み続けると、データが無いのか権限が無いのか区別できない。
        """

        user = getattr(request, "user", None)

        if user is None or not user.is_authenticated:
            return None

        selected_id = request.session.get(PROJECT_SESSION_KEY)

        if not selected_id:
            return None

        from apps.projects.selectors import projects_for

        project = projects_for(user, request.tenant).filter(pk=selected_id).first()

        if project is None:
            # 参照できなくなった選択は残さない。次のリクエストで再判定させない。
            request.session.pop(PROJECT_SESSION_KEY, None)

        return project
