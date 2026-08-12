"""管理者ロールの付与と取り消し。

開発DBのロールをシェルから直接書き換えた履歴があるが、その操作は再現できず、
誰がいつ何を変えたかも残らない。**手順をコマンドとして固定し、実行の記録が
残る形にする**のがこのコマンドの目的。

パスワードや秘密情報は一切扱わない・出力しない。
この構成にはパスワード認証が無く、ロールの変更に本人の資格情報は要らない。

    python manage.py promote_admin --email pmo@example.com
    python manage.py promote_admin --email pmo@example.com --role system_admin
    python manage.py promote_admin --email pmo@example.com --revoke
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.constants import Role
from apps.accounts.models import User

#: 付与できるロール。一般ロールへの付け替えはこのコマンドの仕事ではない。
GRANTABLE_ROLES = (Role.TENANT_ADMIN, Role.SYSTEM_ADMIN)

#: `--revoke` の戻り先。既定値の `viewer` ではなく、昇格前の通常業務ロールへ戻す。
REVOKED_ROLE = Role.PMO


class Command(BaseCommand):
    help = "利用者のロールを管理者へ変更します（--revoke で PMO へ戻します）。"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True, help="対象のメールアドレス")
        parser.add_argument(
            "--role",
            default=Role.TENANT_ADMIN.value,
            choices=[role.value for role in GRANTABLE_ROLES],
            help="付与するロール（既定: tenant_admin）",
        )
        parser.add_argument(
            "--revoke",
            action="store_true",
            help=f"管理者ロールを外し、{REVOKED_ROLE.value} へ戻す",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        email = (options.get("email") or "").strip()
        if not email:
            raise CommandError("--email が空です。対象のメールアドレスを指定してください。")

        user = self._find_user(email)
        target = REVOKED_ROLE.value if options["revoke"] else options["role"]
        before = user.role

        # 冪等。同じ状態なら書き込まず、何もしなかったことを明示して正常終了する。
        if before == target:
            self.stdout.write(f"変更なし: {email} は既に {target} です。")
            return

        user.role = target
        user.save(update_fields=["role"])
        self.stdout.write(
            self.style.SUCCESS(f"変更しました: {email} のロール {before} -> {target}")
        )

    def _find_user(self, email: str) -> User:
        """対象を1件に確定する。曖昧なら止める（黙って何もしない、をしない）。"""

        matches = list(User.objects.filter(email__iexact=email)[:2])
        if not matches:
            raise CommandError(f"利用者が見つかりません: {email}")
        if len(matches) > 1:
            raise CommandError(f"同じメールアドレスの利用者が複数います: {email}")
        return matches[0]
