"""決定的な Work Plan・Step・EvidenceBundle の生成。

LLM を必須にしない。Work Item の `kind` ごとに固定のテンプレートから
Step を組み立てる（LLM が使える場合の高度化は別チケットの対象で、ここは
「LLM 無しでも P0 種別全てが計画を作れる」ことを保証する決定的経路）。

Plan 作成直後に `services.policy` の判定を通し、`services.workflow` で
Work Item を `planned` → `awaiting_confirmation` / `auto_running` へ
遷移させるところまでを一連の処理として持つ（H-04: 根拠競合時に
Work Item が awaiting_confirmation で止まることを、ここで担保する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Max

from apps.pmo_automation.models import (
    AutomationLevel,
    EvidenceBundle,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkPlan,
    WorkStep,
)
from apps.pmo_automation.services import policy, workflow


class EvidenceError(ValueError):
    """出所の無い主張を保存しようとしたことを表す。"""


@dataclass(frozen=True)
class StepTemplate:
    kind: str
    automation_level: str


#: kind別の決定的Stepテンプレート。P0の7種別すべてを、LLM無しでカバーする。
_KIND_STEP_TEMPLATES: dict[str, tuple[StepTemplate, ...]] = {
    WorkKind.DETECTION_TRIAGE: (StepTemplate("internal_draft", AutomationLevel.INTERNAL_APPLY),),
    WorkKind.DATA_QUALITY_REPAIR: (StepTemplate("confirmation_request", AutomationLevel.CONFIRM),),
    WorkKind.FORECAST_REVIEW: (StepTemplate("recalculation", AutomationLevel.INTERNAL_APPLY),),
    WorkKind.REPORT_CYCLE: (StepTemplate("draft_report", AutomationLevel.INTERNAL_APPLY),),
    WorkKind.APPROVAL_FOLLOWUP: (StepTemplate("reminder_draft", AutomationLevel.INTERNAL_APPLY),),
    WorkKind.INTEGRATION_RECOVERY: (StepTemplate("retry_plan", AutomationLevel.INTERNAL_APPLY),),
    WorkKind.KNOWLEDGE_QUALITY: (StepTemplate("reindex_candidate", AutomationLevel.INTERNAL_APPLY),),
}


def record_evidence(
    work_item: PmoWorkItem,
    *,
    source_type: str,
    source_ref: str,
    scope: dict,
    content_hash: str,
    captured_at: datetime,
    expires_at: datetime | None = None,
    confidence: float | None = None,
    conflict_group: str = "",
    agent_run=None,
) -> EvidenceBundle:
    """根拠を保存する。出所（source_ref/scope/content_hash）の無い主張は保存しない。"""

    if not source_ref:
        raise EvidenceError("source_ref の無い根拠は保存できません。")
    if not scope:
        raise EvidenceError("scope（テナント・案件・対象）の無い根拠は保存できません。")
    if not content_hash:
        raise EvidenceError("content_hash の無い根拠は保存できません。")

    return EvidenceBundle.objects.create(
        work_item=work_item,
        source_type=source_type,
        source_ref=source_ref,
        scope=scope,
        content_hash=content_hash,
        captured_at=captured_at,
        expires_at=expires_at,
        confidence=confidence,
        conflict_group=conflict_group,
        agent_run=agent_run,
    )


def build_plan(work_item: PmoWorkItem) -> WorkPlan:
    """`work_item.kind` に応じた決定的テンプレートから Plan と Step を組む。

    Plan は追記型（提案.md 4.1）のため、既存の最大 version + 1 を新版とする。
    """

    templates = _KIND_STEP_TEMPLATES.get(work_item.kind)
    if templates is None:
        raise ValueError(f"未知の kind です（テンプレート未定義）: {work_item.kind}")

    next_version = (
        WorkPlan.objects.filter(work_item=work_item).aggregate(Max("version"))["version__max"] or 0
    ) + 1
    plan_level = (
        AutomationLevel.CONFIRM
        if any(t.automation_level == AutomationLevel.CONFIRM for t in templates)
        else AutomationLevel.INTERNAL_APPLY
    )

    plan = WorkPlan.objects.create(
        work_item=work_item,
        version=next_version,
        automation_level=plan_level,
        summary=f"{work_item.get_kind_display()} の決定的計画 v{next_version}（LLM不使用）",
    )

    for order, template in enumerate(templates, start=1):
        WorkStep.objects.create(
            plan=plan,
            order=order,
            kind=template.kind,
            automation_level=template.automation_level,
            idempotency_key=f"{work_item.dedupe_key}:{plan.version}:{order}",
        )

    return plan


def create_plan_and_evaluate(
    work_item: PmoWorkItem, *, evidence_bundles: list[EvidenceBundle], now: datetime
) -> WorkPlan:
    """Plan を作成し、その場で実行可否を評価して Work Item の状態を進める。

    根拠が競合・期限切れなら awaiting_confirmation に倒す（H-04）。
    全 Step が observe/internal_apply なら auto_running まで進める。
    どちらでもなければ planned のまま留める（承認待ち等は別チケットで扱う）。

    呼び出し前提: `assessing → planned` に必要な
    `policy.guard_assessing_to_planned`（tenant/project/dedupe_key/
    policy_snapshot/evidence scope の検証）は、Work Item を作る側
    （intake.py, PA-04）が担保する。この関数はあくまで「planned に
    到達した Work Item に対する Plan 生成と直後の評価」だけを担う。
    """

    plan = build_plan(work_item)
    workflow.transition_work_item(work_item, WorkItemState.PLANNED)

    steps = list(plan.steps.all())
    confirm_guard = policy.guard_planned_to_awaiting_confirmation(
        steps=steps, evidence_bundles=evidence_bundles, now=now
    )
    if confirm_guard.passed:
        workflow.transition_work_item(work_item, WorkItemState.AWAITING_CONFIRMATION)
        work_item.block_reason = confirm_guard.reason
        work_item.save(update_fields=["block_reason", "updated_at"])
        return plan

    auto_guard = policy.guard_planned_to_auto_running(steps=steps)
    if auto_guard.passed:
        workflow.transition_work_item(work_item, WorkItemState.AUTO_RUNNING)

    return plan
