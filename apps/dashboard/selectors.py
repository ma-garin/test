"""ダッシュボード配下の一覧クエリ。

テナント分離を画面ごとに書くと必ずどこかで漏れるため、参照系はすべて
「参照可能な案件 QuerySet」を入口に取る形でここへ集約する。
呼び出し側は `apps.projects.selectors.projects_for()` の結果を渡すこと。
ここで案件を跨いだ絞り込みを完結させ、サービス層は集計だけに専念させる。
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import F, QuerySet
from django.utils import timezone

from apps.dashboard.models import InterventionProposal, KpiMeasurement
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Project,
    QualityMetric,
    Risk,
    WbsTask,
)

#: 期限接近とみなす日数。管制ダッシュボードの集計と同じ基準に揃えている。
DUE_SOON_DAYS = 7

#: 進行中と見なさないタスク状態。期限超過の判定から除外する。
FINISHED_TASK_STATUSES = (WbsTask.Status.DONE, WbsTask.Status.ARCHIVED)


def tasks_for(
    projects: QuerySet[Project],
    *,
    owner: str = "",
    status: str = "",
    priority: str = "",
    due: str = "",
    progress: str = "",
) -> QuerySet[WbsTask]:
    """WBS タスクの一覧。絞り込み条件は未指定なら無視する。

    条件を空文字で受けるのは、GET パラメータをそのまま渡せるようにするため。
    ビュー側で分岐を書くと画面ごとに条件の解釈がずれる。
    """

    queryset = WbsTask.objects.filter(project__in=projects).select_related("project")

    # アーカイブは「一覧から外す」ための状態。既定で出すとアーカイブの意味が無く、
    # 集計側（`facts` / `input_rules` / `progress`）は除外しているため、
    # 同じ画面の中で件数が食い違う。明示的に選んだときだけ出す。
    if status != WbsTask.Status.ARCHIVED:
        queryset = queryset.exclude(status=WbsTask.Status.ARCHIVED)

    if owner:
        queryset = queryset.filter(owner__icontains=owner)

    if status:
        queryset = queryset.filter(status=status)

    if priority:
        queryset = queryset.filter(priority=priority)

    queryset = _filter_by_due(queryset, due)
    queryset = _filter_by_progress(queryset, progress)

    return queryset.order_by("project__code", "wbs_code")


def _filter_by_due(queryset: QuerySet[WbsTask], due: str) -> QuerySet[WbsTask]:
    """期限による絞り込み。完了済みは期限超過に数えない。"""

    today = timezone.localdate()

    if due == "overdue":
        return queryset.filter(planned_end__lt=today).exclude(status__in=FINISHED_TASK_STATUSES)

    if due == "due_soon":
        limit = today + timedelta(days=DUE_SOON_DAYS)
        return queryset.filter(planned_end__range=(today, limit)).exclude(
            status__in=FINISHED_TASK_STATUSES
        )

    if due == "none":
        return queryset.filter(planned_end__isnull=True)

    return queryset


def _filter_by_progress(queryset: QuerySet[WbsTask], progress: str) -> QuerySet[WbsTask]:
    """進捗率による絞り込み。0 / 途中 / 100 の 3 区分で十分足りる。"""

    if progress == "not_started":
        return queryset.filter(progress_percent__lte=0)

    if progress == "running":
        return queryset.filter(progress_percent__gt=0, progress_percent__lt=100)

    if progress == "completed":
        return queryset.filter(progress_percent__gte=100)

    return queryset


def blocked_tasks_for(projects: QuerySet[Project]) -> QuerySet[WbsTask]:
    """ブロック中タスク。着手できない＝介入対象なので優先度の高い順に並べる。"""

    return (
        WbsTask.objects.filter(project__in=projects, status=WbsTask.Status.BLOCKED)
        .select_related("project")
        .order_by("planned_end", "wbs_code")
    )


def delay_candidate_tasks_for(projects: QuerySet[Project]) -> QuerySet[WbsTask]:
    """遅延見込みタスク。

    「期限を過ぎた未完了」に加えて「期限が近いのに進捗が半分未満」も含める。
    顕在化した遅れだけを見ていると介入が常に後手になるため。
    """

    today = timezone.localdate()
    soon = today + timedelta(days=DUE_SOON_DAYS)

    return (
        WbsTask.objects.filter(project__in=projects, planned_end__lte=soon)
        .exclude(status__in=FINISHED_TASK_STATUSES)
        .exclude(planned_end__gte=today, progress_percent__gte=50)
        .select_related("project")
        .order_by("planned_end", "wbs_code")
    )


def risks_for(projects: QuerySet[Project], *, status: str = "") -> QuerySet[Risk]:
    """リスク一覧。影響度×確率のスコア順。

    スコアはモデルのプロパティだが、並べ替えを Python 側でやると件数が増えたときに
    効かなくなるため、DB 側で同じ式を注釈する。プロパティ名と衝突しないよう別名。
    """

    queryset = Risk.objects.filter(project__in=projects).select_related("project")

    if status:
        queryset = queryset.filter(status=status)

    return queryset.annotate(risk_score=F("probability") * F("impact")).order_by(
        "-risk_score", "due_date"
    )


def change_requests_for(projects: QuerySet[Project], *, status: str = "") -> QuerySet[ChangeRequest]:
    """変更要求一覧。スケジュール影響の大きい順。"""

    queryset = (
        ChangeRequest.objects.filter(project__in=projects)
        .select_related("project", "decided_by")
        .prefetch_related("affected_tasks")
    )

    if status:
        queryset = queryset.filter(status=status)

    return queryset.order_by(F("schedule_impact_days").desc(nulls_last=True), "-created_at")


def interventions_for(
    projects: QuerySet[Project], *, status: str = ""
) -> QuerySet[InterventionProposal]:
    """AI 介入提案の一覧。判断待ちを先に見せたいので状態順ではなく新しい順。"""

    queryset = InterventionProposal.objects.filter(project__in=projects).select_related(
        "project", "alert", "decided_by"
    )

    if status:
        queryset = queryset.filter(status=status)

    return queryset.order_by("-created_at")


def defects_for(projects: QuerySet[Project]) -> QuerySet[Defect]:
    """不具合一覧。"""

    return Defect.objects.filter(project__in=projects).select_related("project")


def quality_metrics_for(projects: QuerySet[Project]) -> QuerySet[QualityMetric]:
    """品質指標。最新値を取り出しやすいよう計測日の新しい順で返す。"""

    return (
        QualityMetric.objects.filter(project__in=projects)
        .select_related("project")
        .order_by("project__code", "metric_key", "-measured_on")
    )


def kpi_measurements_for(projects: QuerySet[Project]) -> QuerySet[KpiMeasurement]:
    """KPI 実績。指標ごとの最新値を取るため計測日の新しい順。"""

    return (
        KpiMeasurement.objects.filter(project__in=projects)
        .select_related("project")
        .order_by("kind", "-measured_on")
    )
