"""案件 1 件から、成果物の材料になる「事実」を集める。

ここは数え方だけを持つ層。文章は作らない。理由は 2 つある。

1. 週次報告・月次報告・障害サマリーで同じ数字を別々に数えると必ずズレる
2. 数字ごとに `EvidenceItem` を作らせることで、根拠の付け忘れを構造的に防げる
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.utils import timezone

from apps.dashboard.models import Alert
from apps.projects.models import Defect, Issue, QualityMetric, Risk, Severity, WbsTask

from apps.pmo.services.generators.base import EvidenceItem, percent

#: リスクスコア（発生確率 × 影響度、各 1-5）がこの値以上なら「高リスク」。
HIGH_RISK_SCORE = 15


@dataclass
class ProjectFacts:
    """案件 1 件の実測値。すべて DB から数えた値で、推定値は入れない。"""

    project: object
    today: date

    task_total: int = 0
    task_done: int = 0
    task_blocked: int = 0
    task_overdue: int = 0
    task_overdue_critical: int = 0
    task_progress_percent: float = 0.0
    overdue_tasks: list = field(default_factory=list)

    issue_open: int = 0
    issue_high: int = 0
    issue_overdue: int = 0
    open_issues: list = field(default_factory=list)

    risk_open: int = 0
    risk_high: int = 0
    risk_materialized: int = 0
    high_risks: list = field(default_factory=list)

    defect_total: int = 0
    defect_open: int = 0
    defect_by_severity: dict = field(default_factory=dict)
    defect_open_by_severity: dict = field(default_factory=dict)
    defect_by_phase: dict = field(default_factory=dict)
    open_defects: list = field(default_factory=list)

    metrics: list = field(default_factory=list)
    metric_miss: list = field(default_factory=list)

    alert_open: int = 0
    alert_critical: int = 0
    open_alerts: list = field(default_factory=list)

    milestones: list = field(default_factory=list)
    milestone_late: int = 0

    evidence: list = field(default_factory=list)

    @property
    def has_material(self) -> bool:
        """1 件でも材料があるか。全部 0 なら「材料が無い」と伝える。"""

        return any(
            [
                self.task_total,
                self.issue_open,
                self.risk_open,
                self.defect_total,
                len(self.metrics),
                self.alert_open,
                len(self.milestones),
            ]
        )

    @property
    def task_done_percent(self) -> float:
        return percent(self.task_done, self.task_total)


def collect_facts(project, today: date | None = None) -> ProjectFacts:
    """案件の実データを 1 度だけ読み、以降の生成で使い回す。"""

    today = today or timezone.localdate()
    facts = ProjectFacts(project=project, today=today)

    _collect_tasks(facts, project, today)
    _collect_issues(facts, project, today)
    _collect_risks(facts, project)
    _collect_defects(facts, project)
    _collect_metrics(facts, project)
    _collect_alerts(facts, project)
    _collect_milestones(facts, project, today)

    return facts


def _collect_tasks(facts: ProjectFacts, project, today: date) -> None:
    tasks = list(WbsTask.objects.filter(project=project).exclude(status=WbsTask.Status.ARCHIVED))
    facts.task_total = len(tasks)
    facts.task_done = sum(1 for t in tasks if t.status == WbsTask.Status.DONE)
    facts.task_blocked = sum(1 for t in tasks if t.status == WbsTask.Status.BLOCKED)

    overdue = [
        t
        for t in tasks
        if t.planned_end
        and t.planned_end < today
        and t.status != WbsTask.Status.DONE
    ]
    facts.overdue_tasks = overdue
    facts.task_overdue = len(overdue)
    facts.task_overdue_critical = sum(1 for t in overdue if t.is_critical_path)

    if tasks:
        total = sum((t.progress_percent or Decimal("0")) for t in tasks)
        facts.task_progress_percent = round(float(total) / len(tasks), 1)

    if not tasks:
        return

    facts.evidence.append(
        EvidenceItem(
            source="projects.WbsTask",
            label="進捗率",
            detail=(
                f"平均進捗 {facts.task_progress_percent}% ＝ タスク {facts.task_total}件の"
                f"progress_percent の平均。完了 {facts.task_done}件"
                f"（完了率 {facts.task_done_percent}%）"
            ),
            count=facts.task_total,
        )
    )

    if overdue:
        facts.evidence.append(
            EvidenceItem(
                source="projects.WbsTask",
                label="期限超過タスク",
                detail=(
                    f"planned_end < {today} かつ未完了 = {facts.task_overdue}件。"
                    f"うち is_critical_path=True が {facts.task_overdue_critical}件。"
                    f"WBS: {', '.join(t.wbs_code for t in overdue[:5])}"
                ),
                count=facts.task_overdue,
            )
        )


def _collect_issues(facts: ProjectFacts, project, today: date) -> None:
    open_statuses = [Issue.Status.OPEN, Issue.Status.IN_PROGRESS, Issue.Status.BLOCKED]
    issues = list(Issue.objects.filter(project=project, status__in=open_statuses))
    facts.open_issues = issues
    facts.issue_open = len(issues)
    facts.issue_high = sum(
        1 for i in issues if i.severity in (Severity.HIGH, Severity.CRITICAL)
    )
    facts.issue_overdue = sum(1 for i in issues if i.due_date and i.due_date < today)

    if issues:
        facts.evidence.append(
            EvidenceItem(
                source="projects.Issue",
                label="未解決の課題",
                detail=(
                    f"status in (未対応/対応中/ブロック中) = {facts.issue_open}件。"
                    f"重大度 高・重大 {facts.issue_high}件、期限超過 {facts.issue_overdue}件"
                ),
                count=facts.issue_open,
            )
        )


def _collect_risks(facts: ProjectFacts, project) -> None:
    open_statuses = [
        Risk.Status.IDENTIFIED,
        Risk.Status.MONITORING,
        Risk.Status.MITIGATING,
        Risk.Status.MATERIALIZED,
    ]
    risks = list(Risk.objects.filter(project=project, status__in=open_statuses))
    facts.risk_open = len(risks)
    facts.risk_materialized = sum(1 for r in risks if r.status == Risk.Status.MATERIALIZED)
    facts.high_risks = [r for r in risks if r.probability * r.impact >= HIGH_RISK_SCORE]
    facts.risk_high = len(facts.high_risks)

    if risks:
        facts.evidence.append(
            EvidenceItem(
                source="projects.Risk",
                label="オープンなリスク",
                detail=(
                    f"クローズ以外 = {facts.risk_open}件。"
                    f"発生確率×影響度 >= {HIGH_RISK_SCORE} が {facts.risk_high}件、"
                    f"顕在化 {facts.risk_materialized}件"
                ),
                count=facts.risk_open,
            )
        )


def _collect_defects(facts: ProjectFacts, project) -> None:
    defects = list(Defect.objects.filter(project=project))
    facts.defect_total = len(defects)
    open_defects = [d for d in defects if d.status != Defect.Status.CLOSED]
    facts.open_defects = open_defects
    facts.defect_open = len(open_defects)

    for severity, label in Severity.choices:
        count = sum(1 for d in defects if d.severity == severity)

        if count:
            facts.defect_by_severity[label] = count

        open_count = sum(1 for d in open_defects if d.severity == severity)

        if open_count:
            facts.defect_open_by_severity[label] = open_count

    for defect in defects:
        phase = defect.phase or "未記入"
        facts.defect_by_phase[phase] = facts.defect_by_phase.get(phase, 0) + 1

    if defects:
        breakdown = "、".join(f"{k} {v}件" for k, v in facts.defect_by_severity.items())
        facts.evidence.append(
            EvidenceItem(
                source="projects.Defect",
                label="不具合の重大度別件数",
                detail=(
                    f"総数 {facts.defect_total}件（{breakdown}）。"
                    f"未クローズ {facts.defect_open}件"
                ),
                count=facts.defect_total,
            )
        )

        phases = "、".join(f"{k} {v}件" for k, v in facts.defect_by_phase.items())
        facts.evidence.append(
            EvidenceItem(
                source="projects.Defect",
                label="検出工程の分布",
                detail=f"phase 別の件数: {phases}",
                count=len(facts.defect_by_phase),
            )
        )


def _collect_metrics(facts: ProjectFacts, project) -> None:
    """指標キーごとに最新の 1 件だけを採る。過去分まで並べても報告には使えないため。"""

    latest: dict[str, QualityMetric] = {}

    for metric in QualityMetric.objects.filter(project=project).order_by("measured_on"):
        latest[metric.metric_key] = metric

    facts.metrics = list(latest.values())
    facts.metric_miss = [m for m in facts.metrics if _misses_target(m)]

    if facts.metrics:
        facts.evidence.append(
            EvidenceItem(
                source="projects.QualityMetric",
                label="品質指標",
                detail=(
                    f"指標キー {len(facts.metrics)}種の最新値。"
                    f"目標未達 {len(facts.metric_miss)}種"
                ),
                count=len(facts.metrics),
            )
        )


def _misses_target(metric: QualityMetric) -> bool:
    """目標値との比較。目標が無い指標は判定しない（未達扱いにしない）。"""

    if metric.target_value is None:
        return False

    if metric.higher_is_better:
        return metric.value < metric.target_value

    return metric.value > metric.target_value


def _collect_alerts(facts: ProjectFacts, project) -> None:
    alerts = list(
        Alert.objects.filter(
            project=project, status__in=[Alert.Status.OPEN, Alert.Status.ACKNOWLEDGED]
        )
    )
    facts.open_alerts = alerts
    facts.alert_open = len(alerts)
    facts.alert_critical = sum(1 for a in alerts if a.severity == Alert.Severity.CRITICAL)

    if alerts:
        facts.evidence.append(
            EvidenceItem(
                source="dashboard.Alert",
                label="未解消アラート",
                detail=(
                    f"未対応・確認済み = {facts.alert_open}件。"
                    f"うち重大 {facts.alert_critical}件"
                ),
                count=facts.alert_open,
            )
        )


def _collect_milestones(facts: ProjectFacts, project, today: date) -> None:
    from apps.projects.models import Milestone

    milestones = list(Milestone.objects.filter(project=project).order_by("planned_date"))
    facts.milestones = milestones
    facts.milestone_late = sum(
        1
        for m in milestones
        if m.actual_date is None
        and (m.forecast_date or m.planned_date) > m.planned_date
        or (m.actual_date and m.actual_date > m.planned_date)
    )

    if milestones:
        facts.evidence.append(
            EvidenceItem(
                source="projects.Milestone",
                label="マイルストーン",
                detail=(
                    f"{len(milestones)}件。計画日より後ろ倒し（見込 or 実績）が"
                    f" {facts.milestone_late}件"
                ),
                count=len(milestones),
            )
        )
