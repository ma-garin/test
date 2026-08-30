"""テナント・ユーザー・ロール。

旧実装ではテナントと利用者はセッション上のモック値だった
（`access_flow.MOCK_USER_OPTIONS`）。Django 版では認証・認可の実体として持つ。
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.accounts.constants import APPROVER_ROLES, Role
from apps.core.models import TimeStampedModel


class Tenant(TimeStampedModel):
    """利用組織。データ参照範囲の分離境界になる。"""

    code = models.SlugField("テナントコード", max_length=64, unique=True)
    name = models.CharField("テナント名", max_length=200)
    description = models.TextField("説明", blank=True)
    is_active = models.BooleanField("有効", default=True)

    class Meta:
        verbose_name = "テナント"
        verbose_name_plural = "テナント"
        ordering = ["code"]

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    """利用者。テナントとロールを必ず持つ。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # ログイン識別子。パスワードを使わないため、一意であることが本人特定の唯一の根拠になる。
    email = models.EmailField("メールアドレス", unique=True)
    tenant = models.ForeignKey(
        Tenant,
        verbose_name="テナント",
        on_delete=models.PROTECT,
        related_name="users",
        null=True,
        blank=True,
    )
    display_name = models.CharField("表示名", max_length=120, blank=True)
    role = models.CharField(
        "ロール",
        max_length=32,
        choices=Role.choices,
        default=Role.VIEWER,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        verbose_name = "利用者"
        verbose_name_plural = "利用者"

    def __str__(self) -> str:
        return self.display_name or self.get_username()

    @property
    def can_approve(self) -> bool:
        """承認操作（`Action.APPROVE`）を実行できるか。"""

        return self.is_superuser or self.role in APPROVER_ROLES

    @property
    def is_tenant_admin(self) -> bool:
        return self.is_superuser or self.role in (Role.TENANT_ADMIN, Role.SYSTEM_ADMIN)
