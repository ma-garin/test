"""AI 介入提案に対する人の判断を記録する。

判断は履歴そのものなので、上書きを許さない。すでに判断済みの提案に対する
更新は例外にして、呼び出し側へ「もう決まっている」と返す。
更新は `QuerySet.update()` で行い、渡されたインスタンスは書き換えない
（呼び出し側が古い値と新しい値を比較できるようにするため）。
"""

from __future__ import annotations

from django.utils import timezone

from apps.audit.models import OperationLog
from apps.dashboard.forms import DECIDABLE_STATUSES
from apps.dashboard.models import InterventionProposal

#: 操作ログに残すときの操作名。
DECIDE_ACTION = "intervention.decide"


class AlreadyDecidedError(RuntimeError):
    """判断済みの提案を再判断しようとした。"""


class InvalidDecisionError(ValueError):
    """判断として成立しない入力。"""


def is_pending(proposal: InterventionProposal) -> bool:
    """まだ人の判断が入っていないか。"""

    return proposal.status == InterventionProposal.Status.PROPOSED


def decide_intervention(
    proposal: InterventionProposal,
    *,
    user,
    status: str,
    decision_reason: str,
    modified_action: str = "",
) -> InterventionProposal:
    """提案の採否を確定し、判断者・判断日時・理由とともに保存する。

    返すのは保存後の新しいインスタンス。引数の `proposal` は変更しない。
    """

    if status not in DECIDABLE_STATUSES:
        raise InvalidDecisionError(f"判断として使えない状態です: {status}")

    reason = (decision_reason or "").strip()

    if not reason:
        raise InvalidDecisionError("判断理由は必須です。")

    if not is_pending(proposal):
        raise AlreadyDecidedError("この提案はすでに判断済みです。")

    action = (modified_action or "").strip()
    values = {
        "status": status,
        "decided_by": user if getattr(user, "is_authenticated", False) else None,
        "decided_at": timezone.now(),
        "decision_reason": reason,
        "modified_action": action
        if status == InterventionProposal.Status.MODIFIED
        else proposal.modified_action,
    }

    # 状態を条件に含めることで、同時に 2 人が判断しても先勝ちになる。
    updated = InterventionProposal.objects.filter(
        pk=proposal.pk, status=InterventionProposal.Status.PROPOSED
    ).update(**values)

    if not updated:
        raise AlreadyDecidedError("この提案はすでに判断済みです。")

    decided = InterventionProposal.objects.select_related(
        "project", "project__tenant", "decided_by"
    ).get(pk=proposal.pk)

    _log(decided, user=user, reason=reason)

    return decided


def _log(proposal: InterventionProposal, *, user, reason: str) -> None:
    """誰が何を判断したかを操作ログにも残す。監査画面から追えるようにする。"""

    OperationLog.objects.create(
        tenant=proposal.project.tenant,
        user=user if getattr(user, "is_authenticated", False) else None,
        project=proposal.project,
        action=DECIDE_ACTION,
        target=f"{proposal.get_status_display()} / {proposal.title}",
        succeeded=True,
        detail=reason,
    )
