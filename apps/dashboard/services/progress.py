"""進捗予測・介入画面の集計。

「計画実績差分」を案件の `progress_percent` だけで語ると、計画がどこまで
進んでいるはずかが分からない。そこで WBS の計画終了日を基準に
「今日までに終わっているはずのタスク割合」を計画線として算出し、
実績（完了タスク割合）との差をとる。AI は使わず実データだけで決定する。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.projects.models import Project, WbsTask

#: 一覧に出す遅延見込み・ブロック中タスクの上限。
MAX_TASKS = 20


@dataclass(frozen=True)
class ProgressRow:
    """1 案件の計画実績差分。"""

    project: Project
    total_tasks: int
    planned_percent: int
    actual_percent: int
    overdue_tasks: int
    blocked_tasks: int

    @property
    def gap(self) -> int:
        """実績 - 計画。負なら遅れている。"""

        return self.actual_percent - self.planned_percent

    @property
    def tone(self) -> str:
        """差分の色。10 ポイント以上の遅れを要対応とする。"""

        if self.gap <= -10:
            return "r"

        return "a" if self.gap < 0 else "g"

    @property
    def forecast_label(self) -> str:
        if self.total_tasks == 0:
            return "タスク未登録"

        return {"r": "遅延見込み", "a": "やや遅れ", "g": "計画どおり"}[self.tone]


@dataclass(frozen=True)
class ProgressReport:
    rows: tuple[ProgressRow, ...]
    delayed_tasks: tuple[WbsTask, ...]
    blocked_tasks: tuple[WbsTask, ...]

    @property
    def delayed_projects(self) -> int:
        return sum(1 for row in self.rows if row.gap < 0)

    @property
    def worst(self) -> ProgressRow | None:
        return min(self.rows, key=lambda row: row.gap) if self.rows else None

    @property
    def average_gap(self) -> int:
        if not self.rows:
            return 0

        return round(sum(row.gap for row in self.rows) / len(self.rows))


def build_progress_report(
    projects: QuerySet[Project],
    delayed_tasks: QuerySet[WbsTask],
    blocked_tasks: QuerySet[WbsTask],
) -> ProgressReport:
    """案件ごとの計画実績差分と、介入候補のタスクをまとめる。"""

    today = timezone.localdate()
    finished = [WbsTask.Status.DONE, WbsTask.Status.ARCHIVED]

    annotated = projects.annotate(
        task_total=Count("wbstask_set", distinct=True),
        task_done=Count(
            "wbstask_set", filter=Q(wbstask_set__status__in=finished), distinct=True
        ),
        task_expected=Count(
            "wbstask_set", filter=Q(wbstask_set__planned_end__lte=today), distinct=True
        ),
        task_overdue=Count(
            "wbstask_set",
            filter=Q(wbstask_set__planned_end__lt=today) & ~Q(wbstask_set__status__in=finished),
            distinct=True,
        ),
        task_blocked=Count(
            "wbstask_set", filter=Q(wbstask_set__status=WbsTask.Status.BLOCKED), distinct=True
        ),
    )

    rows = tuple(_build_row(project) for project in annotated)

    return ProgressReport(
        rows=rows,
        delayed_tasks=tuple(delayed_tasks[:MAX_TASKS]),
        blocked_tasks=tuple(blocked_tasks[:MAX_TASKS]),
    )


def _build_row(project: Project) -> ProgressRow:
    """注釈済みの案件から 1 行を作る。タスク 0 件でもゼロ除算しない。"""

    total = project.task_total

    return ProgressRow(
        project=project,
        total_tasks=total,
        planned_percent=round(100 * project.task_expected / total) if total else 0,
        actual_percent=round(100 * project.task_done / total) if total else 0,
        overdue_tasks=project.task_overdue,
        blocked_tasks=project.task_blocked,
    )
