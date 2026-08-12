"""RAGの評価結果悪化を PMO Work Item として取り込む(PA-12: RAG品質修復)。

`apps.rag.models.EvaluationRun` が評価不能（evaluable=False）の場合に
`knowledge_quality` Work Item として取り込む。`unavailable_reason` は
既に文字列として記録済みのため、その値をそのまま確認依頼の根拠として使い、
根拠の内容を推測で補わない（提案.md A-16: 根拠不足は回答成功で終わらせず、
索引／文書確認の仕事に変換する）。

`WorkLink` は既存モデル（Alert 等）用の固定 FK 構成であり
`EvaluationRun` 用の参照を持たないため（PA-09 の ForecastSnapshot と同じ
制約）、ここでは `WorkLink` を経由せず、`source_type`/`source_key` と
`block_reason` で対象を追跡する。
"""

from __future__ import annotations

from django.utils import timezone

from apps.pmo_automation.models import PmoWorkItem, WorkKind
from apps.pmo_automation.services.intake import IntakeResult, build_dedupe_key
from apps.pmo_automation.services.rate_limit import check_intake_rate_limit
from apps.rag.models import EvaluationRun


class RagIntakeError(ValueError):
    """intake 対象外の EvaluationRun を表す。"""


def intake_from_rag_evaluation_degradation(
    evaluation_run: EvaluationRun, *, dry_run: bool = False
) -> IntakeResult:
    """評価不能（evaluable=False）の EvaluationRun を knowledge_quality として取り込む。

    評価可能（evaluable=True）な実行は、たとえ pass_rate 等が低くても対象外にする。
    「悪化」をどの閾値で判定するかは運用ポリシーの決定が要るため、ここでは
    P0スコープとして「評価不能」という明確な安全条件だけを対象にし、
    推測で閾値を決めない。
    """

    if evaluation_run.evaluable:
        raise RagIntakeError("evaluable=True の EvaluationRun は intake 対象外（P0スコープ外）。")

    if evaluation_run.project_id is None:
        raise RagIntakeError(
            "案件未指定（テナント全体）の評価実行は、帰属先の案件を推測できないため intake 対象外。"
        )

    tenant = evaluation_run.tenant
    project = evaluation_run.project
    dedupe_key = build_dedupe_key(source_type="rag_evaluation", source_key=str(evaluation_run.pk))

    existing = PmoWorkItem.objects.filter(
        tenant=tenant, dedupe_key=dedupe_key, is_active=True
    ).first()
    if existing is not None:
        # 既存レコードを返すのは読み取りのみであり、dry-run不変条件には抵触しない。
        return IntakeResult(work_item=existing, created=False, dedupe_key=dedupe_key)

    check_intake_rate_limit(tenant, now=timezone.now())

    if dry_run:
        return IntakeResult(work_item=None, created=True, dedupe_key=dedupe_key)

    work_item = PmoWorkItem.objects.create(
        tenant=tenant,
        project=project,
        kind=WorkKind.KNOWLEDGE_QUALITY,
        source_type="rag_evaluation",
        source_key=str(evaluation_run.pk),
        dedupe_key=dedupe_key,
        block_reason=evaluation_run.unavailable_reason or "評価不能（理由が記録されていません）。",
    )

    return IntakeResult(work_item=work_item, created=True, dedupe_key=dedupe_key)
