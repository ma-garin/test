"""Work Item の状態機械、承認失効、職務分掌。

状態遷移は必ずこのモジュールを経由する。`PmoWorkItem.objects.filter(...).update()`
のような QuerySet.update での書き換えは、allowed_transitions の検証を素通り
できてしまうため使わない。ここでは常にモデルインスタンスの属性を更新して
`.save()` する。
"""

from __future__ import annotations

from datetime import datetime

from apps.pmo_automation.models import ApprovalRequest, ApprovalStatus, PmoWorkItem
from apps.pmo_automation.services import policy

# docs/agent/pmo_autopilot_contract.json の allowed_transitions と一対一。
# 値がずれていないことは test_workflow.py で契約ファイルと突き合わせて検証する。
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "new": ("assessing", "cancelled"),
    "assessing": ("planned", "hold", "cancelled"),
    "planned": ("auto_running", "awaiting_confirmation", "awaiting_approval", "hold", "cancelled"),
    "auto_running": (
        "awaiting_confirmation",
        "awaiting_approval",
        "completed",
        "retry_scheduled",
        "hold",
        "failed",
    ),
    "awaiting_confirmation": ("planned", "awaiting_approval", "hold", "cancelled"),
    "awaiting_approval": ("executing", "planned", "hold", "cancelled"),
    "executing": ("completed", "retry_scheduled", "hold", "failed"),
    "retry_scheduled": ("auto_running", "executing", "hold", "failed"),
    "failed": ("retry_scheduled", "hold"),
    "completed": (),
    "cancelled": (),
    "hold": ("planned",),
}

TERMINAL_STATES = frozenset({"completed", "cancelled", "hold"})

#: 人が能動的に下せる決定。EXPIRED は自動失効専用、PENDING は初期状態のため、
#: どちらも decide_approval の入力としては受け付けない（レビュー指摘の入力検証漏れ対応）。
_VALID_DECISIONS = frozenset(
    {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.RETURNED, ApprovalStatus.HOLD}
)


class TransitionError(ValueError):
    """契約の allowed_transitions にない遷移を表す。"""


class SelfApprovalError(PermissionError):
    """作成者・最後の実行者による自己承認を表す（職務分掌違反）。"""


def transition_work_item(work_item: PmoWorkItem, target_state: str) -> PmoWorkItem:
    """`work_item.state` を検証つきで書き換える。

    terminal state（completed/cancelled/hold）へ入るときは is_active を
    False にし、hold から planned へ戻るときは True に戻す
    （dedupe 制約は is_active=True のレコードだけを対象にするため）。
    """

    current_state = work_item.state
    allowed = ALLOWED_TRANSITIONS.get(current_state, ())
    if target_state not in allowed:
        raise TransitionError(
            f"{current_state} から {target_state} への遷移は allowed_transitions にありません。"
        )

    work_item.state = target_state
    work_item.is_active = target_state not in TERMINAL_STATES
    work_item.save(update_fields=["state", "is_active", "updated_at"])
    return work_item


def expire_approval_if_stale(
    approval: ApprovalRequest,
    *,
    current_plan_version: int,
    approved_evidence_hash: str,
    current_evidence_hash: str,
    approved_policy_version: int | None = None,
    current_policy_version: int | None = None,
) -> bool:
    """plan版・根拠hash・policy版のいずれかが承認時から変われば EXPIRED にする（H-07）。

    ApprovalRequest は「承認時点のplan版」しか保持しないため、根拠hashと
    policy版は呼び出し側（PA-04/PA-08で実装予定のintake/実行コマンド）が
    承認時点の値と現在値の両方を渡す。
    """

    if approval.status != ApprovalStatus.APPROVED:
        return False

    plan_changed = approval.plan_version != current_plan_version
    evidence_changed = approved_evidence_hash != current_evidence_hash
    policy_changed = (
        approved_policy_version is not None
        and current_policy_version is not None
        and approved_policy_version != current_policy_version
    )

    if not (plan_changed or evidence_changed or policy_changed):
        return False

    reasons = []
    if plan_changed:
        reasons.append("plan版")
    if evidence_changed:
        reasons.append("根拠hash")
    if policy_changed:
        reasons.append("policy版")

    approval.status = ApprovalStatus.EXPIRED
    approval.decision_reason = "、".join(reasons) + "が承認時から変わったため自動失効。"
    approval.save(update_fields=["status", "decision_reason", "updated_at"])
    return True


def decide_approval(
    approval: ApprovalRequest,
    *,
    actor_id,
    decision: str,
    decided_by,
    decision_reason: str,
    now: datetime,
) -> ApprovalRequest:
    """PENDING の承認依頼に対する人の判断を記録する。

    高リスク操作を作成した者・最後に実行した者は、自分の作成した承認を
    決定できない（H-08）。この自己承認チェックは
    `policy.guard_awaiting_approval_to_executing` 内の判定と同じ趣旨だが、
    あちらは「承認済みの依頼がまだ有効か」を見る別の遷移
    （awaiting_approval→executing）向けのガードであり、
    ここでの「これから承認するかどうかの決定」には直接使えないため、
    同じ判定条件をこの関数内に持つ（allowed_paths の制約上、
    policy.py 側に共通化のための切り出しはしていない）。
    """

    if approval.status != ApprovalStatus.PENDING:
        raise ValueError(f"pending 以外の ApprovalRequest は決定できません（status={approval.status}）。")
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"decision は {sorted(_VALID_DECISIONS)} のいずれかである必要があります（decision={decision}）。")

    if decision == ApprovalStatus.APPROVED:
        if actor_id is not None and actor_id in (approval.created_by_id, approval.last_executed_by_id):
            raise SelfApprovalError("作成者または最後の実行者は自己承認できません。")

    approval.status = decision
    approval.decided_by = decided_by
    approval.decided_at = now
    approval.decision_reason = decision_reason
    approval.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
    return approval


def can_execute(
    approval: ApprovalRequest,
    *,
    plan_version: int,
    evidence_bundles,
    expected_evidence_hash: str,
    actual_evidence_hash: str,
    actor_id,
    actor_role: str,
    required_role: str,
    now: datetime,
) -> policy.GuardResult:
    """awaiting_approval → executing の遷移条件を判定する。

    policy.py の guard をそのまま使う（awaiting_approval_to_executing は
    「承認済みの依頼が今も有効か」を見るガードであり、ここでの用途と一致する）。
    """

    return policy.guard_awaiting_approval_to_executing(
        approval=approval,
        plan_version=plan_version,
        evidence_bundles=evidence_bundles,
        expected_evidence_hash=expected_evidence_hash,
        actual_evidence_hash=actual_evidence_hash,
        actor_id=actor_id,
        actor_role=actor_role,
        required_role=required_role,
        now=now,
    )
