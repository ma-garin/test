"""E2E用のロール別利用者を投入する（冪等）。

使い方: .venv/bin/python manage.py shell < e2e/seed_role_users.py

認証はパスワードレス（メールアドレスのみ）のためパスワードは設定しない。
未知メールの自動作成は一律 PMO担当ロールになるため（apps/accounts/backends.py）、
権限テストにはこのスクリプトでロール付きの利用者を先に作っておく必要がある。
"""

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User

pmo = User.objects.filter(email="pmo@example.com").first()
tenant = (pmo.tenant if pmo and pmo.tenant_id else None) or (
    Tenant.objects.filter(is_active=True).order_by("created_at").first()
)
assert tenant is not None, "有効なテナントがありません。先に make seed を実行してください。"

SPECS = [
    ("pm@example.com", Role.PROJECT_MANAGER, "E2E PM・PL", True),
    ("quality@example.com", Role.QUALITY_MANAGER, "E2E 品質責任者", True),
    ("change@example.com", Role.CHANGE_MANAGER, "E2E 変更管理者", True),
    ("viewer@example.com", Role.VIEWER, "E2E 参照のみ", True),
    ("tenantadmin@example.com", Role.TENANT_ADMIN, "E2E テナント管理者", True),
    ("sysadmin@example.com", Role.SYSTEM_ADMIN, "E2E システム管理者", True),
    # TC-AUTH-053（無効化済み利用者の拒否）検証用
    ("deactivated@example.com", Role.VIEWER, "E2E 無効化済み", False),
]

for email, role, name, active in SPECS:
    user, created = User.objects.update_or_create(
        email=email,
        defaults={
            "role": role,
            "tenant": tenant,
            "display_name": name,
            "is_active": active,
            "username": email.split("@")[0],
        },
    )
    print(f"{email}: role={role} active={active} {'created' if created else 'updated'}")
