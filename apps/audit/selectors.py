"""監査データの参照クエリ。

テナント分離をここへ集約する。ビューやサービスが `Feedback.objects` を
直接触ると、テナント条件の付け忘れが各所へ散るため。
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.audit.models import Feedback, OperationLog


def _resolve_tenant(user, tenant):
    """参照対象テナントを決める。

    テナント切替 UI で未選択（`request.tenant` が None）の場合でも、所属テナントが
    あればそれを使う。どちらも無い利用者は「何も見えない」が正解なので None を返す。
    """

    return tenant or getattr(user, "tenant", None)


def feedbacks_for(user, tenant) -> QuerySet[Feedback]:
    """閲覧可能なフィードバック。テナント未確定なら空集合。"""

    resolved = _resolve_tenant(user, tenant)

    if resolved is None:
        return Feedback.objects.none()

    return Feedback.objects.filter(tenant=resolved).select_related("user")


def operation_logs_for(user, tenant) -> QuerySet[OperationLog]:
    """閲覧可能な操作ログ。テナント未確定なら空集合。"""

    resolved = _resolve_tenant(user, tenant)

    if resolved is None:
        return OperationLog.objects.none()

    return OperationLog.objects.filter(tenant=resolved).select_related("user", "project")
