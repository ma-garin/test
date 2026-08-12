"""不具合の登録・クローズ。

物理削除はしない。取り下げ・終了は状態（closed）で表現する。
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.models import OperationLog
from apps.projects.models import Defect


@transaction.atomic
def save_defect(form, *, user) -> Defect:
    """フォームの内容で不具合を作成・更新する。"""

    is_create = form.instance.pk is None
    defect = form.save()

    _log(
        defect,
        user=user,
        action="defect.create" if is_create else "defect.update",
        detail=f"状態={defect.get_status_display()} / 重大度={defect.get_severity_display()}",
    )

    return defect


@transaction.atomic
def close_defect(defect: Defect, *, user) -> Defect:
    """不具合をクローズする（論理的な終了。レコードは残す）。"""

    defect.status = Defect.Status.CLOSED
    defect.closed_on = defect.closed_on or timezone.localdate()
    defect.save(update_fields=["status", "closed_on", "updated_at"])

    _log(defect, user=user, action="defect.close", detail=f"完了日={defect.closed_on}")

    return defect


def _log(defect: Defect, *, user, action: str, detail: str) -> None:
    OperationLog.objects.create(
        tenant=defect.project.tenant,
        user=user if getattr(user, "is_authenticated", False) else None,
        project=defect.project,
        action=action,
        target=f"不具合 {defect.pk} {defect.title}"[:300],
        succeeded=True,
        detail=detail,
    )
