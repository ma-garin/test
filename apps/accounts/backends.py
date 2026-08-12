"""メールアドレスだけで認証するバックエンド。

パスワードを検証せず、未登録のメールアドレスはその場で利用者を作る。
つまり誰でもログインできる。体験環境向けの割り切りであり、
所有確認（マジックリンク、SSO）を入れるまで本番では使えない。

Django admin へのログインは `ModelBackend` が担当し、パスワードを要求する。
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest

#: 自動作成した利用者に与えるロール。体験目的なので画面を一通り見られるものにする。
VISITOR_ROLE = "pmo"


class EmailOnlyBackend(BaseBackend):
    """メールアドレスで利用者を特定する。存在しなければ作る。"""

    def authenticate(
        self,
        request: HttpRequest | None = None,
        email: str | None = None,
        **kwargs,
    ):
        if not email:
            return None

        normalized = email.strip()
        user = get_user_model().objects.filter(email__iexact=normalized).first()

        if user is None:
            return self._create_visitor(normalized)

        if not user.is_active:
            return None

        return user

    def get_user(self, user_id):
        return get_user_model().objects.filter(pk=user_id).first()

    def _create_visitor(self, email: str):
        """初めて見るメールアドレスの利用者を作る。"""

        from apps.accounts.models import Tenant

        user_model = get_user_model()
        local_part = email.split("@")[0][:100] or "user"

        user = user_model(
            email=email,
            username=self._available_username(local_part),
            display_name=local_part,
            tenant=Tenant.objects.filter(is_active=True).order_by("created_at").first(),
            role=VISITOR_ROLE,
        )
        # ログインにパスワードを使わないので、使用不能な値を入れる。
        user.set_unusable_password()
        user.save()

        return user

    def _available_username(self, base: str) -> str:
        """`username` は unique なので、衝突したら連番を足す。"""

        user_model = get_user_model()
        candidate = base
        suffix = 1

        while user_model.objects.filter(username=candidate).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"

        return candidate
