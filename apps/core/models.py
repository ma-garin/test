"""全アプリで共有する抽象モデル。

再設計時の方針に合わせ、業務データは必ず以下を満たす。

- 外部公開しても安全な UUID を主キーにする
- 作成・更新時刻を持つ
- 物理削除ではなく状態遷移で「対象外」を表現する（旧 index_map.json の考え方を継承）
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField("作成日時", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=False)


class SoftDeleteModel(TimeStampedModel):
    """論理削除。原本は残し、参照対象から外すだけにする。"""

    deleted_at = models.DateTimeField("削除日時", null=True, blank=True, db_index=True)

    objects = SoftDeleteQuerySet.as_manager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, *, save: bool = True) -> None:
        self.deleted_at = timezone.now()

        if save:
            self.save(update_fields=["deleted_at", "updated_at"])

    def restore(self, *, save: bool = True) -> None:
        self.deleted_at = None

        if save:
            self.save(update_fields=["deleted_at", "updated_at"])


class TenantOwnedModel(TimeStampedModel):
    """テナント境界を持つモデル。

    参照分離はアプリ層の責務にせず、必ずこのフィールドで絞り込む。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )

    class Meta:
        abstract = True
