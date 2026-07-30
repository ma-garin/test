"""チャットへ渡す案件文脈。

RAG の検索結果だけでは「この案件の課題は？」に答えられない。登録文書に
書かれていない現在値（進捗・未解決課題・リスク・期限超過）は DB にしかないため、
応答の冒頭へ事実として添える。

ここでも LLM は使わない（ADR-0003）。集計と定型文の組み立てだけで完結させる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import F
from django.utils import timezone

from apps.projects.models import Defect, Issue, Project, Risk, WbsTask

#: 未解決とみなす状態。クローズ相当を除外する。
OPEN_ISSUE_STATUSES = (Issue.Status.OPEN, Issue.Status.IN_PROGRESS, Issue.Status.BLOCKED)
OPEN_RISK_STATUSES = (
    Risk.Status.IDENTIFIED,
    Risk.Status.MONITORING,
    Risk.Status.MITIGATING,
    Risk.Status.MATERIALIZED,
)
OPEN_DEFECT_STATUSES = (
    Defect.Status.NEW,
    Defect.Status.ANALYZING,
    Defect.Status.FIXING,
    Defect.Status.VERIFYING,
)
OPEN_TASK_STATUSES = (
    WbsTask.Status.NOT_STARTED,
    WbsTask.Status.IN_PROGRESS,
    WbsTask.Status.BLOCKED,
)

#: 冒頭に並べる件名の数。多いと応答の本体が埋もれる。
HEADLINE_LIMIT = 3

#: 応答冒頭の見出し。テストと画面表示で共有する。
CONTEXT_HEADING = "いま見ている案件の状況"


@dataclass(frozen=True)
class ProjectContext:
    """案件の現在値。数値と主要件名だけを持ち、判断は書かない。"""

    project: Project
    progress_percent: float
    open_issues: int
    open_risks: int
    open_defects: int
    overdue_tasks: int
    top_issues: list[str] = field(default_factory=list)
    top_risks: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        """応答の冒頭に差し込む文面。"""

        lines = [
            f"【{CONTEXT_HEADING}】{self.project.name}（{self.project.code}）",
            f"・進捗率 {self.progress_percent:.0f}% / "
            f"状態 {self.project.get_status_display()}（{self.project.get_rag_status_display()}）",
            f"・未解決の課題 {self.open_issues}件 / 未クローズのリスク {self.open_risks}件 / "
            f"未完了の不具合 {self.open_defects}件 / 期限超過タスク {self.overdue_tasks}件",
        ]

        if self.top_issues:
            lines.append("・注目課題: " + " / ".join(self.top_issues))

        if self.top_risks:
            lines.append("・注目リスク: " + " / ".join(self.top_risks))

        return "\n".join(lines)


def build(project: Project | None) -> ProjectContext | None:
    """案件の現在値を集計する。案件が選ばれていなければ None。"""

    if project is None:
        return None

    issues = Issue.objects.filter(project=project, status__in=OPEN_ISSUE_STATUSES)
    risks = Risk.objects.filter(project=project, status__in=OPEN_RISK_STATUSES)
    defects = Defect.objects.filter(project=project, status__in=OPEN_DEFECT_STATUSES)
    overdue = WbsTask.objects.filter(
        project=project,
        status__in=OPEN_TASK_STATUSES,
        planned_end__lt=timezone.localdate(),
    )

    return ProjectContext(
        project=project,
        progress_percent=float(project.progress_percent or 0),
        open_issues=issues.count(),
        open_risks=risks.count(),
        open_defects=defects.count(),
        overdue_tasks=overdue.count(),
        # 期限の近いものから見せる。期限未設定は最後に回す。
        top_issues=list(
            issues.order_by(F("due_date").asc(nulls_last=True)).values_list("title", flat=True)[
                :HEADLINE_LIMIT
            ]
        ),
        top_risks=list(
            risks.order_by("-impact", "-probability").values_list("title", flat=True)[
                :HEADLINE_LIMIT
            ]
        ),
    )
