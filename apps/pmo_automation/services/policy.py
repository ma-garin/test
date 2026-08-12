"""自動化ポリシー評価。

`docs/agent/pmo_autopilot_contract.json` の `automation_levels` と
`transition_guards` を実装する純粋関数の集合。ここでは判定結果を返すだけで、
DB書き込み・状態遷移の実行・外部コネクタ呼び出しは一切行わない
（実際の状態遷移は `services/workflow.py` が担当する）。

すべての判定は「わからなければ安全側（hold または awaiting_confirmation）」
に倒す。呼び出し側が現在時刻を渡すため、この関数群自体は
`timezone.now()` を呼ばない（決定的・テスト可能にするため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apps.pmo_automation.models import (
    ApprovalRequest,
    ApprovalStatus,
    AutomationLevel,
    EvidenceBundle,
    WorkItemState,
    WorkStep,
    WorkStepState,
)


@dataclass(frozen=True)
class PolicyDecision:
    """allow/deny の二値だけでなく、次に取るべき状態と理由まで返す。"""

    allow: bool
    level: str
    next_state: str
    reason: str
    failure_category: str = ""


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    reason: str = ""


# --- 根拠の鮮度・競合 -------------------------------------------------------


def evidence_is_fresh(evidence: EvidenceBundle, *, now: datetime) -> bool:
    if evidence.expires_at is None:
        return True

    return evidence.expires_at > now


def evidence_bundle_is_fresh(evidence_bundles: list[EvidenceBundle], *, now: datetime) -> bool:
    """一件でも欠落・期限切れがあれば、安全側へ倒して「鮮度なし」とみなす。"""

    if not evidence_bundles:
        return False

    return all(evidence_is_fresh(evidence, now=now) for evidence in evidence_bundles)


def evidence_has_conflict(evidence_bundles: list[EvidenceBundle]) -> bool:
    """同じ conflict_group に複数の根拠がある場合、競合として扱う。"""

    groups: dict[str, set] = {}
    for evidence in evidence_bundles:
        if not evidence.conflict_group:
            continue
        groups.setdefault(evidence.conflict_group, set()).add(evidence.pk)

    return any(len(pks) > 1 for pks in groups.values())


# --- Step 実行直前の評価（FR-02: 画面表示時の判定結果を信用しない） --------


def evaluate_step(*, step: WorkStep, evidence_bundles: list[EvidenceBundle], now: datetime) -> PolicyDecision:
    """Step を今この瞬間に自動実行してよいかを判定する。"""

    if step.automation_level == AutomationLevel.PROHIBITED:
        return PolicyDecision(
            allow=False,
            level=AutomationLevel.PROHIBITED,
            next_state=WorkItemState.HOLD,
            reason="automation_level が prohibited のため自動実行しない。",
            failure_category="policy",
        )

    if evidence_has_conflict(evidence_bundles):
        return PolicyDecision(
            allow=False,
            level=step.automation_level,
            next_state=WorkItemState.AWAITING_CONFIRMATION,
            reason="根拠が競合しているため人の確認が必要。",
        )

    if not evidence_bundle_is_fresh(evidence_bundles, now=now):
        return PolicyDecision(
            allow=False,
            level=step.automation_level,
            next_state=WorkItemState.AWAITING_CONFIRMATION,
            reason="根拠が欠落または期限切れのため人の確認が必要。",
        )

    if step.automation_level in (AutomationLevel.OBSERVE, AutomationLevel.INTERNAL_APPLY):
        return PolicyDecision(
            allow=True,
            level=step.automation_level,
            next_state=WorkItemState.AUTO_RUNNING,
            reason="observe/internal_apply の範囲内のため自動実行する。",
        )

    if step.automation_level == AutomationLevel.CONFIRM:
        return PolicyDecision(
            allow=False,
            level=step.automation_level,
            next_state=WorkItemState.AWAITING_CONFIRMATION,
            reason="confirm レベルのため人の確認を待つ。",
        )

    if step.automation_level == AutomationLevel.APPROVE:
        return PolicyDecision(
            allow=False,
            level=step.automation_level,
            next_state=WorkItemState.AWAITING_APPROVAL,
            reason="approve レベルのため権限者の明示承認を待つ。",
        )

    return PolicyDecision(
        allow=False,
        level=step.automation_level,
        next_state=WorkItemState.HOLD,
        reason=f"未知の automation_level: {step.automation_level}",
        failure_category="policy",
    )


# --- transition_guards（contract.json と一対一） ---------------------------


def guard_assessing_to_planned(
    *, tenant_id, project_id, dedupe_key: str, policy_snapshot: dict, evidence_bundles: list[EvidenceBundle]
) -> GuardResult:
    if not tenant_id or not project_id:
        return GuardResult(False, "tenant/project が未確定。")
    if not dedupe_key:
        return GuardResult(False, "dedupe_key が未確定。")
    if not policy_snapshot:
        return GuardResult(False, "policy_snapshot が未評価。")
    if not evidence_bundles:
        return GuardResult(False, "evidence_bundle が存在しない。")
    if any(not evidence.scope for evidence in evidence_bundles):
        return GuardResult(False, "scope（テナント・案件・対象）が明示されていない根拠がある。")

    return GuardResult(True)


def guard_planned_to_auto_running(*, steps: list[WorkStep]) -> GuardResult:
    if not steps:
        return GuardResult(False, "Step が存在しない。")
    if all(step.automation_level in (AutomationLevel.OBSERVE, AutomationLevel.INTERNAL_APPLY) for step in steps):
        return GuardResult(True)

    return GuardResult(False, "observe/internal_apply 以外の Step を含む。")


def guard_planned_to_awaiting_confirmation(
    *, steps: list[WorkStep], evidence_bundles: list[EvidenceBundle], now: datetime
) -> GuardResult:
    has_confirm_step = any(step.automation_level == AutomationLevel.CONFIRM for step in steps)
    is_stale = not evidence_bundle_is_fresh(evidence_bundles, now=now)
    has_conflict = evidence_has_conflict(evidence_bundles)

    if has_confirm_step or is_stale or has_conflict:
        return GuardResult(True, "confirm Step、または根拠の期限切れ・競合がある。")

    return GuardResult(False)


def guard_awaiting_approval_to_executing(
    *,
    approval: ApprovalRequest,
    plan_version: int,
    evidence_bundles: list[EvidenceBundle],
    expected_evidence_hash: str,
    actual_evidence_hash: str,
    actor_id,
    actor_role: str,
    required_role: str,
    now: datetime,
) -> GuardResult:
    if approval.status != ApprovalStatus.APPROVED:
        return GuardResult(False, "承認されていない。")
    if approval.expires_at is not None and approval.expires_at <= now:
        return GuardResult(False, "承認が失効している。")
    if approval.plan_version != plan_version:
        return GuardResult(False, "plan 版が承認時と一致しない。")
    if expected_evidence_hash != actual_evidence_hash:
        return GuardResult(False, "根拠の内容が承認時から変わっている。")
    if not evidence_bundle_is_fresh(evidence_bundles, now=now):
        return GuardResult(False, "根拠が期限切れ。")
    if required_role and actor_role != required_role:
        return GuardResult(False, "実行者が必要ロールを満たさない。")
    if actor_id is not None and actor_id in (approval.created_by_id, approval.last_executed_by_id):
        return GuardResult(False, "作成者または最後の実行者は自己承認できない。")

    return GuardResult(True)


def guard_any_to_completed(*, steps: list[WorkStep], evidence_bundles: list[EvidenceBundle]) -> GuardResult:
    for step in steps:
        if step.state not in (WorkStepState.SUCCEEDED, WorkStepState.SKIPPED):
            return GuardResult(False, f"Step {step.order} が未完了状態（{step.state}）。")
    if evidence_has_conflict(evidence_bundles):
        return GuardResult(False, "未解決の根拠競合があるため completed にできない。")

    return GuardResult(True)


def guard_hold_to_planned(*, human_reason: str, policy_reevaluated: bool) -> GuardResult:
    if not human_reason:
        return GuardResult(False, "解除には人の理由が必要。")
    if not policy_reevaluated:
        return GuardResult(False, "現在の policy を再評価していない。")

    return GuardResult(True)
