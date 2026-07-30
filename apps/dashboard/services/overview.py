"""管制ダッシュボードの集計。

画面側で ORM を叩かず、ここで集計済みの構造を作る。テストが書きやすく、
将来 API 化するときもそのまま流用できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Count, Q, QuerySet

from apps.dashboard.models import Alert, InterventionProposal
from apps.projects.models import Issue, Project, Risk, WbsTask


@dataclass
class ProjectSummary:
    project: Project
    open_issues: int = 0
    open_risks: int = 0
    delayed_tasks: int = 0
    open_alerts: int = 0
    pending_proposals: int = 0


@dataclass
class Overview:
    summaries: list[ProjectSummary] = field(default_factory=list)
    critical_alerts: list[Alert] = field(default_factory=list)

    @property
    def project_count(self) -> int:
        return len(self.summaries)

    @property
    def total_open_issues(self) -> int:
        return sum(summary.open_issues for summary in self.summaries)

    @property
    def total_open_risks(self) -> int:
        return sum(summary.open_risks for summary in self.summaries)


def build_overview(projects: QuerySet[Project]) -> Overview:
    """案件ごとの集計と、重要アラートの一覧を返す。"""

    annotated = projects.annotate(
        open_issue_count=Count(
            "issue_set",
            filter=~Q(issue_set__status__in=[Issue.Status.RESOLVED, Issue.Status.CLOSED]),
            distinct=True,
        ),
        open_risk_count=Count(
            "risk_set",
            filter=~Q(risk_set__status=Risk.Status.CLOSED),
            distinct=True,
        ),
        blocked_task_count=Count(
            "wbstask_set",
            filter=Q(wbstask_set__status=WbsTask.Status.BLOCKED),
            distinct=True,
        ),
        open_alert_count=Count(
            "alerts",
            filter=Q(alerts__status=Alert.Status.OPEN),
            distinct=True,
        ),
        pending_proposal_count=Count(
            "interventions",
            filter=Q(interventions__status=InterventionProposal.Status.PROPOSED),
            distinct=True,
        ),
    )

    summaries = [
        ProjectSummary(
            project=project,
            open_issues=project.open_issue_count,
            open_risks=project.open_risk_count,
            delayed_tasks=project.blocked_task_count,
            open_alerts=project.open_alert_count,
            pending_proposals=project.pending_proposal_count,
        )
        for project in annotated
    ]

    critical_alerts = list(
        Alert.objects.filter(
            project__in=projects,
            status=Alert.Status.OPEN,
            severity__in=[Alert.Severity.CRITICAL, Alert.Severity.WARNING],
        )
        .select_related("project")
        .order_by("-severity", "-detected_at")[:20]
    )

    return Overview(summaries=summaries, critical_alerts=critical_alerts)
