"""管制ダッシュボードの集計。

モック（`docs/screens/VeriRAG_PMO_Agent_MVP.html`）のダッシュボードは
「KPI 4枚 → ヘルススコアと指標 → 重要アラート Top3 → リスク観点ソート表」
という構成。画面側で ORM を叩かず、その形に整えた構造をここで作る。

ヘルススコアは実データから決定的に算出する。AI は使わないので、
値の根拠を `breakdown` として持たせ、画面で説明できるようにしている。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Avg, Count, Q, QuerySet
from django.utils import timezone

from apps.dashboard.models import Alert, InterventionProposal
from apps.documents.models import Document, DocumentStatus
from apps.projects.models import Issue, Project, Risk, WbsTask

#: 期限接近とみなす日数。
DUE_SOON_DAYS = 7

#: 各減点の重み。合計 100 からの引き算でヘルススコアを出す。
PENALTY_BLOCKED_TASK = 8
PENALTY_OVERDUE_TASK = 6
PENALTY_CRITICAL_ISSUE = 10
PENALTY_HIGH_RISK = 6
PENALTY_OPEN_ALERT = 5


@dataclass
class ProjectSummary:
    project: Project
    open_issues: int = 0
    open_risks: int = 0
    blocked_tasks: int = 0
    overdue_tasks: int = 0
    due_soon_tasks: int = 0
    open_alerts: int = 0
    pending_proposals: int = 0
    health_score: int = 100
    breakdown: list[str] = field(default_factory=list)

    @property
    def tone(self) -> str:
        """スコアの色分け。r=要対応 / a=注意 / g=良好。"""

        if self.health_score < 50:
            return "r"

        return "a" if self.health_score < 75 else "g"

    @property
    def tone_label(self) -> str:
        return {"r": "要対応", "a": "注意", "g": "良好"}[self.tone]


@dataclass
class RankedAlert:
    rank: int
    alert: Alert

    @property
    def tone(self) -> str:
        return {
            Alert.Severity.CRITICAL: "r",
            Alert.Severity.WARNING: "a",
            Alert.Severity.INFO: "g",
        }.get(self.alert.severity, "g")


@dataclass
class Overview:
    summaries: list[ProjectSummary] = field(default_factory=list)
    ranked_alerts: list[RankedAlert] = field(default_factory=list)
    average_progress: int = 0
    indexed_documents: int = 0
    total_documents: int = 0
    proposal_count: int = 0

    @property
    def project_count(self) -> int:
        return len(self.summaries)

    @property
    def total_open_issues(self) -> int:
        return sum(summary.open_issues for summary in self.summaries)

    @property
    def total_open_risks(self) -> int:
        return sum(summary.open_risks for summary in self.summaries)

    @property
    def total_blocked_tasks(self) -> int:
        return sum(summary.blocked_tasks for summary in self.summaries)

    @property
    def total_due_soon(self) -> int:
        return sum(summary.due_soon_tasks for summary in self.summaries)

    @property
    def health_score(self) -> int:
        """全案件の平均ヘルススコア。"""

        if not self.summaries:
            return 0

        return round(sum(summary.health_score for summary in self.summaries) / len(self.summaries))

    @property
    def tone(self) -> str:
        if self.health_score < 50:
            return "r"

        return "a" if self.health_score < 75 else "g"

    @property
    def tone_label(self) -> str:
        return {"r": "要対応", "a": "注意", "g": "良好"}[self.tone]

    @property
    def lowest(self) -> ProjectSummary | None:
        return min(self.summaries, key=lambda s: s.health_score) if self.summaries else None

    @property
    def document_index_percent(self) -> int:
        if not self.total_documents:
            return 0

        return round(100 * self.indexed_documents / self.total_documents)

    @property
    def risk_percent(self) -> int:
        """リスク件数のバー表示用。10 件で振り切る目安。"""

        return min(100, self.total_open_risks * 10)


def build_overview(projects: QuerySet[Project]) -> Overview:
    """案件ごとの集計、ヘルススコア、重要アラートの順位付けを返す。"""

    today = timezone.localdate()
    due_soon = today + timezone.timedelta(days=DUE_SOON_DAYS)
    unfinished = ~Q(wbstask_set__status__in=[WbsTask.Status.DONE, WbsTask.Status.ARCHIVED])

    annotated = projects.annotate(
        open_issue_count=Count(
            "issue_set",
            filter=~Q(issue_set__status__in=[Issue.Status.RESOLVED, Issue.Status.CLOSED]),
            distinct=True,
        ),
        critical_issue_count=Count(
            "issue_set",
            filter=Q(issue_set__severity__in=["high", "critical"])
            & ~Q(issue_set__status__in=[Issue.Status.RESOLVED, Issue.Status.CLOSED]),
            distinct=True,
        ),
        open_risk_count=Count(
            "risk_set",
            filter=~Q(risk_set__status=Risk.Status.CLOSED),
            distinct=True,
        ),
        high_risk_count=Count(
            "risk_set",
            filter=Q(risk_set__probability__gte=4, risk_set__impact__gte=4)
            & ~Q(risk_set__status=Risk.Status.CLOSED),
            distinct=True,
        ),
        blocked_task_count=Count(
            "wbstask_set",
            filter=Q(wbstask_set__status=WbsTask.Status.BLOCKED),
            distinct=True,
        ),
        overdue_task_count=Count(
            "wbstask_set",
            filter=Q(wbstask_set__planned_end__lt=today) & unfinished,
            distinct=True,
        ),
        due_soon_task_count=Count(
            "wbstask_set",
            filter=Q(wbstask_set__planned_end__range=(today, due_soon)) & unfinished,
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

    # 危ない案件から見せる。ヘルスは Python 側で算出するため、DB ではなくここで並べる。
    # 画面の見出しも「ヘルス低い順」なので、ここが崩れると表示と実態が食い違う。
    summaries = sorted(
        (_summarize(project) for project in annotated),
        key=lambda summary: summary.health_score,
    )

    ranked_alerts = [
        RankedAlert(rank=position, alert=alert)
        for position, alert in enumerate(
            Alert.objects.filter(project__in=projects, status=Alert.Status.OPEN)
            .select_related("project")
            .order_by("severity", "-detected_at")[:5],
            start=1,
        )
    ]

    documents = Document.objects.filter(
        project__in=projects, status=DocumentStatus.ACTIVE, deleted_at__isnull=True
    )
    tenant_ids = {project.tenant_id for project in annotated}

    if tenant_ids:
        # 案件に紐づかないテナント共通ナレッジも件数に含める。
        documents = Document.objects.filter(
            tenant_id__in=tenant_ids, status=DocumentStatus.ACTIVE, deleted_at__isnull=True
        )

    average = projects.aggregate(value=Avg("progress_percent"))["value"] or Decimal(0)

    return Overview(
        summaries=summaries,
        ranked_alerts=ranked_alerts,
        average_progress=round(float(average)),
        indexed_documents=documents.filter(last_indexed_at__isnull=False).count(),
        total_documents=documents.count(),
        proposal_count=sum(summary.pending_proposals for summary in summaries),
    )


def _summarize(project: Project) -> ProjectSummary:
    """1 案件のヘルススコアを算出する。

    100 点から、状態の悪さに応じて引いていく。どの項目で何点引いたかを
    `breakdown` に残し、画面で根拠を示せるようにする。
    """

    penalties = [
        (project.blocked_task_count, PENALTY_BLOCKED_TASK, "ブロック中タスク"),
        (project.overdue_task_count, PENALTY_OVERDUE_TASK, "期限超過タスク"),
        (project.critical_issue_count, PENALTY_CRITICAL_ISSUE, "重大課題"),
        (project.high_risk_count, PENALTY_HIGH_RISK, "高スコアリスク"),
        (project.open_alert_count, PENALTY_OPEN_ALERT, "未対応アラート"),
    ]

    score = 100
    breakdown: list[str] = []

    for count, weight, label in penalties:
        if not count:
            continue

        deduction = count * weight
        score -= deduction
        breakdown.append(f"{label} {count}件 -{deduction}")

    return ProjectSummary(
        project=project,
        open_issues=project.open_issue_count,
        open_risks=project.open_risk_count,
        blocked_tasks=project.blocked_task_count,
        overdue_tasks=project.overdue_task_count,
        due_soon_tasks=project.due_soon_task_count,
        open_alerts=project.open_alert_count,
        pending_proposals=project.pending_proposal_count,
        health_score=max(0, min(100, score)),
        breakdown=breakdown or ["減点なし"],
    )
