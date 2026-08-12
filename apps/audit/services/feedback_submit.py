"""フィードバックの登録。

保存と同時に操作ログを残す。誰がいつ評価したかは監査対象であり、
フィードバック自体が消えても操作の事実は追えるようにしておく。
本文のマスクは `Feedback.save()` / `OperationLog.save()` 側で行われる。
"""

from __future__ import annotations

from apps.audit.models import Feedback, OperationLog

#: 操作ログに残すときの操作名。
SUBMIT_ACTION = "feedback.submit"


class MissingTenantError(ValueError):
    """テナントが特定できない状態で投稿しようとした。"""


def submit_feedback(
    *,
    tenant,
    user,
    rating,
    comment: str = "",
    has_fact_error: bool = False,
    answer=None,
    agent_run=None,
) -> Feedback:
    """フィードバックを 1 件登録し、保存済みインスタンスを返す。"""

    if tenant is None:
        raise MissingTenantError("テナントが特定できないため、フィードバックを登録できません。")

    author = user if getattr(user, "is_authenticated", False) else None
    feedback = Feedback.objects.create(
        tenant=tenant,
        user=author,
        answer=answer,
        agent_run=agent_run,
        rating=int(rating),
        comment=(comment or "").strip(),
        has_fact_error=bool(has_fact_error),
    )

    OperationLog.objects.create(
        tenant=tenant,
        user=author,
        action=SUBMIT_ACTION,
        target=f"{feedback.get_rating_display()} / 事実誤認{'あり' if feedback.has_fact_error else 'なし'}",
        succeeded=True,
        detail=feedback.comment,
    )

    return feedback
