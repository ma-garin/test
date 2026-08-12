"""report_cycle Work Item から、次版の下書き・差分・事実チェックを作る。

締め時刻や周期そのもののスケジューリングは扱わない（D-02: 実案件の周期設定は
人が決めるまで作らない）。ここは「今この瞬間に report_cycle を実行する」
という単発の処理だけを提供する。

本文の生成方法自体は変更せず、既存の `apps.pmo.services.generators` /
`diffing` / `fact_check` をそのまま呼び出す（同じ数字の数え方を二重に
持たないため）。承認済みの `Deliverable` は一切更新せず、常に新しい版を
`draft` として作る。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from apps.pmo.models import Deliverable
from apps.pmo.services import fact_check as fact_check_service
from apps.pmo.services.diffing import DiffResult, line_diff
from apps.pmo.services.generators.base import spec_for
from apps.pmo.services.generators.facts import collect_facts
from apps.pmo.services.generators.reports import build_report, period_start_for
from apps.pmo_automation.models import ApprovalRequest, EvidenceBundle, PmoWorkItem, WorkLink


class ReportCycleError(ValueError):
    """未知の generator_key など、report_cycle を実行できない状態を表す。"""


@dataclass(frozen=True)
class ReportCycleResult:
    deliverable: Deliverable
    diff: DiffResult
    fact_check: fact_check_service.FactCheckResult
    approval: ApprovalRequest
    previous_deliverable: Deliverable | None


def run_report_cycle(
    work_item: PmoWorkItem, *, generator_key: str, now: datetime
) -> ReportCycleResult:
    """report_cycle Work Item を処理し、次版の下書き・差分・事実チェック・承認依頼を作る。

    - 直近の承認済み Deliverable（あれば）を前回本文として使う。承認済み本文は
      一切更新しない。
    - 本文は `generators.reports.build_report`（facts.py が集計した実データのみ
      使用、LLM不使用）でそのまま生成する。
    - 前回本文との差分は `diffing.line_diff` で計算する。
    - 事実チェックは `fact_check.check` で行う（本文中の数値主張と実データの
      突き合わせ、LLM不使用）。
    - 事実チェック結果は EvidenceBundle として Work Item に紐付けて残す。
    """

    spec = spec_for(generator_key)
    if spec is None:
        raise ReportCycleError(f"未知の generator_key です: {generator_key}")

    project = work_item.project
    today = now.date()

    period_start = period_start_for(generator_key, today)
    facts = collect_facts(project, today, period_start=period_start)
    document = build_report(facts, generator_key)

    previous = (
        Deliverable.objects.filter(
            project=project, kind=spec.deliverable_kind, status=Deliverable.Status.APPROVED
        )
        .order_by("-version")
        .first()
    )
    next_version = (previous.version + 1) if previous else 1
    previous_body = previous.body if previous else ""

    diff = line_diff(previous_body, document.body)

    # 承認済み版（previous）は一切 save/update しない。常に新しいレコードを作る。
    new_deliverable = Deliverable.objects.create(
        project=project,
        kind=spec.deliverable_kind,
        title=document.title,
        version=next_version,
        status=Deliverable.Status.DRAFT,
        ai_generated_body=document.body,
    )

    fact_check_result = fact_check_service.check(new_deliverable, today=today)

    evidence = EvidenceBundle.objects.create(
        work_item=work_item,
        source_type="fact_check",
        source_ref=str(new_deliverable.pk),
        scope={"tenant": work_item.tenant.code, "project": project.code},
        content_hash=hashlib.sha256(fact_check_result.checked_body.encode()).hexdigest(),
        captured_at=now,
    )

    plan_version = work_item.plans.order_by("-version").values_list("version", flat=True).first() or 1

    approval = ApprovalRequest.objects.create(
        work_item=work_item,
        plan_version=plan_version,
        requested_action=f"{spec.label}（v{next_version}）の確定・配布承認",
        diff_summary=_summarize(diff, fact_check_result),
    )

    # WorkLink は「1レコード1ターゲット」が設計上の前提（models.py の
    # WorkLink docstring）。deliverable と approval をまとめて1レコードに
    # 詰めると監査時に「成果物への言及」と「承認依頼への言及」が区別できなく
    # なるため、レコードを分けて作る（レビュー指摘対応）。
    WorkLink.objects.create(work_item=work_item, deliverable=new_deliverable)
    WorkLink.objects.create(work_item=work_item, approval=approval)

    return ReportCycleResult(
        deliverable=new_deliverable,
        diff=diff,
        fact_check=fact_check_result,
        approval=approval,
        previous_deliverable=previous,
    )


def _summarize(diff: DiffResult, fact_check_result: fact_check_service.FactCheckResult) -> str:
    lines = [f"差分: 追記 {diff.added} 行 / 削除 {diff.removed} 行"]
    lines.append(fact_check_result.summary)

    if fact_check_result.unverified:
        unverified_display = "、".join(claim.display for claim in fact_check_result.unverified[:5])
        lines.append(f"未検証の数値: {unverified_display}")

    return "\n".join(lines)
