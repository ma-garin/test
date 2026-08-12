"""品質ゲート失敗（QualityMetric）からのintake。

提案.md A-13: 品質ゲート失敗を、関連する課題・不具合の件数とあわせて
data_quality_repair Work Item へ束ねる。QualityMetric.passes_gate という
既存の検知規則そのものは変更しない。

WorkLink には QualityMetric 用の参照フィールドが無いため（PA-09の
ForecastSnapshot と同じ制約）、既存モデルへのリンクは作らず、
block_reason に安全な集計情報だけを記録する。
"""

from __future__ import annotations

from django.utils import timezone

from apps.pmo_automation.models import PmoWorkItem, WorkKind
from apps.pmo_automation.services.intake import IntakeResult, build_dedupe_key
from apps.pmo_automation.services.rate_limit import check_intake_rate_limit
from apps.projects.models import Defect, Issue, QualityMetric


def intake_from_quality_gate_failure(metric: QualityMetric, *, dry_run: bool = False) -> IntakeResult:
    """品質ゲートに落ちた QualityMetric を data_quality_repair として取り込む。

    threshold 未設定（passes_gate is None）は判定不能として対象外にする
    （値を推測して合否を決めない）。ゲート合格も対象外。
    """

    passes = metric.passes_gate
    if passes is None:
        raise ValueError("threshold が未設定の QualityMetric は判定不能のため intake 対象外。")
    if passes:
        raise ValueError("ゲートに合格している QualityMetric は intake 対象ではない。")

    project = metric.project
    tenant = project.tenant
    dedupe_key = build_dedupe_key(source_type="quality_metric", source_key=str(metric.pk))

    existing = PmoWorkItem.objects.filter(tenant=tenant, dedupe_key=dedupe_key, is_active=True).first()
    if existing is not None:
        return IntakeResult(work_item=existing, created=False, dedupe_key=dedupe_key)

    check_intake_rate_limit(tenant, now=timezone.now())

    if dry_run:
        return IntakeResult(work_item=None, created=True, dedupe_key=dedupe_key)

    open_issue_count = (
        Issue.objects.filter(project=project)
        .exclude(status__in=(Issue.Status.RESOLVED, Issue.Status.CLOSED))
        .count()
    )
    open_defect_count = Defect.objects.filter(project=project).exclude(status=Defect.Status.CLOSED).count()

    work_item = PmoWorkItem.objects.create(
        tenant=tenant,
        project=project,
        kind=WorkKind.DATA_QUALITY_REPAIR,
        source_type="quality_metric",
        source_key=str(metric.pk),
        dedupe_key=dedupe_key,
        block_reason=(
            f"品質ゲート未達（{metric.metric_label or metric.metric_key}）。"
            f"未解決の課題 {open_issue_count} 件、未解決の不具合 {open_defect_count} 件と関連。"
        ),
    )

    return IntakeResult(work_item=work_item, created=True, dedupe_key=dedupe_key)
