"""PMO の参照クエリ。

テナント分離をビューごとに書くと必ずどこかで漏れるため、案件スコープの絞り込みは
`projects_for` を起点にしてここへ集約する。PMO の各モデルはテナントを直接持たない
（案件経由で辿る）ものがあり、その差をビューへ漏らさないことが目的。
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.pmo.models import Approval, Deliverable, PlanDraft, PromptTemplate
from apps.projects.selectors import projects_for


def plan_drafts_for(user, tenant) -> QuerySet[PlanDraft]:
    """参照できる案件の計画ドラフト。"""

    return PlanDraft.objects.filter(project__in=projects_for(user, tenant)).select_related(
        "project", "agent_run"
    )


def deliverables_for(user, tenant) -> QuerySet[Deliverable]:
    """参照できる案件の成果物。

    根拠評価は承認可否の判定に毎行必要になるので、ここで一緒に引いておく。
    """

    return (
        Deliverable.objects.filter(
            project__in=projects_for(user, tenant), deleted_at__isnull=True
        )
        .select_related("project", "created_by", "agent_run", "agent_run__evidence")
    )


def deliverables_awaiting_decision_for(user, tenant) -> QuerySet[Deliverable]:
    """承認判断が必要な成果物（下書き・承認待ち）。"""

    return deliverables_for(user, tenant).filter(
        status__in=[Deliverable.Status.DRAFT, Deliverable.Status.PENDING_APPROVAL]
    )


def approvals_for(user, tenant) -> QuerySet[Approval]:
    """参照できる案件の承認履歴。誰がいつ何をしたかの追跡用。"""

    return Approval.objects.filter(
        deliverable__project__in=projects_for(user, tenant)
    ).select_related("deliverable", "deliverable__project", "actor")


def prompt_templates_for(tenant) -> QuerySet[PromptTemplate]:
    """テナントの有効なプロンプトテンプレート。"""

    if tenant is None:
        return PromptTemplate.objects.none()

    return PromptTemplate.objects.filter(
        tenant=tenant, is_active=True, deleted_at__isnull=True
    )
