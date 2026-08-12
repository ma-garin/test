"""ポリシー変更のシャドー較正（提案.md A-19）。

「フィードバックは一覧で、採否・誤り・効果を次のルール改善に使えない」を
解消するため、過去の Work Item 実績に対して「もし automation_level が
違っていたら、この期間の結果はどう変わっていたか」を DB へ一切書き込まずに
シミュレーションする。分析専用（常にシャドー = 乾式評価）で、
ポリシー変更を実際に適用する処理はここには含まない。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from apps.pmo_automation.models import PmoWorkItem, WorkItemState, WorkStep
from apps.pmo_automation.services import policy


@dataclass(frozen=True)
class CalibrationResult:
    """ある期間の Work Item 実績を、現行 automation_level と候補で比較した結果。

    DB へは一切書き込まない（分析専用）。
    """

    tenant_id: object
    work_kind: str
    period_start: datetime
    period_end: datetime
    total_work_items: int
    current_automation_level: str
    candidate_automation_level: str
    evaluated_step_count: int
    current_state_outcomes: dict[str, int] = field(default_factory=dict)
    candidate_next_state_outcomes: dict[str, int] = field(default_factory=dict)
    delta_summary: str = ""


def _count_current_state_outcomes(work_items) -> dict[str, int]:
    return dict(Counter(work_items.values_list("state", flat=True)))


def calibrate_policy_change(
    tenant,
    *,
    work_kind: str,
    period_start: datetime,
    period_end: datetime,
    current_automation_level: str,
    candidate_automation_level: str,
    now: datetime,
) -> CalibrationResult:
    """指定期間・指定 kind の Work Item 実績に対し、Step の automation_level を
    current から candidate に変えたら `policy.evaluate_step` の判定がどう
    変わっていたかをシャドー計算する。

    対象は「現行の automation_level が current と一致する Step」のみ。
    シミュレーションは取得したモデルインスタンスの属性をメモリ上だけ書き換えて
    `policy.evaluate_step`（純粋関数、DB書き込みなし）に渡すだけで行う。
    `.save()` は一度も呼ばないため、DB は一切変更されない。
    """

    work_items = PmoWorkItem.objects.filter(
        tenant=tenant, kind=work_kind, created_at__gte=period_start, created_at__lt=period_end
    )
    total = work_items.count()
    current_state_outcomes = _count_current_state_outcomes(work_items)

    candidate_next_state_outcomes: Counter[str] = Counter()
    evaluated_step_count = 0

    steps = (
        WorkStep.objects.filter(
            plan__work_item__in=work_items,
            automation_level=current_automation_level,
        )
        .select_related("plan__work_item")
        .prefetch_related("plan__work_item__evidence_bundles")
    )

    for step in steps:
        work_item = step.plan.work_item
        evidence_bundles = list(work_item.evidence_bundles.all())

        # メモリ上だけで automation_level を候補値に差し替える。save() は呼ばない。
        step.automation_level = candidate_automation_level
        decision = policy.evaluate_step(step=step, evidence_bundles=evidence_bundles, now=now)

        candidate_next_state_outcomes[decision.next_state] += 1
        evaluated_step_count += 1

    delta_summary = _summarize(
        total=total,
        evaluated_step_count=evaluated_step_count,
        current_automation_level=current_automation_level,
        candidate_automation_level=candidate_automation_level,
        candidate_next_state_outcomes=candidate_next_state_outcomes,
    )

    return CalibrationResult(
        tenant_id=tenant.id,
        work_kind=work_kind,
        period_start=period_start,
        period_end=period_end,
        total_work_items=total,
        current_automation_level=current_automation_level,
        candidate_automation_level=candidate_automation_level,
        evaluated_step_count=evaluated_step_count,
        current_state_outcomes=current_state_outcomes,
        candidate_next_state_outcomes=dict(candidate_next_state_outcomes),
        delta_summary=delta_summary,
    )


def _summarize(
    *,
    total: int,
    evaluated_step_count: int,
    current_automation_level: str,
    candidate_automation_level: str,
    candidate_next_state_outcomes: Counter,
) -> str:
    if evaluated_step_count == 0:
        return (
            f"対象期間に automation_level={current_automation_level} の Step が"
            "無いため、比較対象がありません。"
        )

    auto_running = candidate_next_state_outcomes.get(WorkItemState.AUTO_RUNNING, 0)
    held_like = evaluated_step_count - auto_running

    return (
        f"Work Item {total} 件中 {evaluated_step_count} Step を "
        f"{current_automation_level} → {candidate_automation_level} で再評価。"
        f"自動実行可能になる見込み {auto_running} 件、"
        f"人の確認・承認が必要なまま {held_like} 件。"
    )
