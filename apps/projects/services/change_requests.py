"""変更要求の登録・判断。

判断（承認・却下）は監査対象。誰が・いつ・何を理由に決めたかを
モデル上（decided_by / decided_at / decision_reason）と操作ログの両方に残す。
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import OperationLog
from apps.projects.models import ChangeRequest

DECISION_STATUS = {
    "approved": ChangeRequest.Status.APPROVED,
    "rejected": ChangeRequest.Status.REJECTED,
}


@transaction.atomic
def save_change_request(form, *, user) -> ChangeRequest:
    """フォームの内容で変更要求を作成・更新する。"""

    is_create = form.instance.pk is None
    change = form.save()

    _log(
        change,
        user=user,
        action="change_request.create" if is_create else "change_request.update",
        detail=f"状態={change.get_status_display()}",
    )

    return change


@transaction.atomic
def decide_change_request(change: ChangeRequest, *, user, decision: str, reason: str) -> ChangeRequest:
    """変更要求を承認・却下する。

    権限のない利用者からの呼び出しは PermissionDenied（403）にする。
    """

    if not getattr(user, "can_approve", False):
        raise PermissionDenied("変更要求を判断する権限がありません。")

    if decision not in DECISION_STATUS:
        raise ValidationError("判断の値が不正です。")

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError("判断理由は必須です。")

    change.status = DECISION_STATUS[decision]
    change.decided_by = user
    change.decided_at = timezone.now()
    change.decision_reason = reason
    change.save(
        update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"]
    )

    _log(
        change,
        user=user,
        action="change_request.decide",
        detail=f"判断={change.get_status_display()} / 理由={reason}",
    )

    return change


def _log(change: ChangeRequest, *, user, action: str, detail: str) -> None:
    OperationLog.objects.create(
        tenant=change.project.tenant,
        user=user if getattr(user, "is_authenticated", False) else None,
        project=change.project,
        action=action,
        target=f"変更要求 {change.pk} {change.title}"[:300],
        succeeded=True,
        detail=detail,
    )
