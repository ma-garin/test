"""タスク一覧画面の集計。

一覧をそのまま出すだけでは「どれから見ればよいか」が分からないため、
期限超過・ブロック中の件数を先に出し、行ごとに強調の色を決めておく。
色の判定を画面側の `{% if %}` に散らすと基準がずれるので、ここで確定させる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import QuerySet
from django.utils import timezone

from apps.projects.models import Priority, WbsTask

#: 一覧の最大表示件数。全件描画すると案件が増えたときに画面が壊れる。
MAX_ROWS = 200

#: 期限接近とみなす日数。
DUE_SOON_DAYS = 7


@dataclass(frozen=True)
class TaskFilters:
    """画面で選択中の絞り込み条件。フォームの選択状態の復元に使う。"""

    owner: str = ""
    status: str = ""
    priority: str = ""
    due: str = ""
    progress: str = ""

    @property
    def is_active(self) -> bool:
        return any([self.owner, self.status, self.priority, self.due, self.progress])


@dataclass(frozen=True)
class TaskRow:
    """1 タスクの表示用データ。"""

    task: WbsTask
    is_overdue: bool
    is_due_soon: bool

    @property
    def tone(self) -> str:
        """期限の強調色。r=超過 / a=接近 / n=通常。"""

        if self.is_overdue:
            return "r"

        return "a" if self.is_due_soon else "n"

    @property
    def status_tone(self) -> str:
        if self.task.status == WbsTask.Status.BLOCKED:
            return "r"

        if self.task.status == WbsTask.Status.IN_PROGRESS:
            return "b"

        return "g" if self.task.status == WbsTask.Status.DONE else "n"

    @property
    def priority_tone(self) -> str:
        return {Priority.URGENT: "r", Priority.HIGH: "a", Priority.MEDIUM: "b"}.get(
            self.task.priority, "n"
        )


@dataclass(frozen=True)
class TaskBoard:
    """タスク一覧画面が必要とするものすべて。"""

    rows: tuple[TaskRow, ...]
    total: int
    overdue: int
    blocked: int
    in_progress: int
    done: int
    filters: TaskFilters

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return WbsTask.Status.choices

    @property
    def priority_choices(self) -> list[tuple[str, str]]:
        return Priority.choices

    @property
    def due_choices(self) -> list[tuple[str, str]]:
        return [("overdue", "期限超過"), ("due_soon", "7日以内"), ("none", "期限未設定")]

    @property
    def progress_choices(self) -> list[tuple[str, str]]:
        return [("not_started", "0%"), ("running", "進行中"), ("completed", "100%")]

    @property
    def is_truncated(self) -> bool:
        return self.total > len(self.rows)

    @property
    def done_percent(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0


def build_task_board(tasks: QuerySet[WbsTask], filters: TaskFilters) -> TaskBoard:
    """絞り込み済みのタスク QuerySet から画面表示用の構造を作る。"""

    today = timezone.localdate()
    soon = today + timedelta(days=DUE_SOON_DAYS)
    materialized = list(tasks[:MAX_ROWS])

    rows = tuple(_build_row(task, today, soon) for task in materialized)

    return TaskBoard(
        rows=rows,
        total=tasks.count(),
        overdue=sum(1 for row in rows if row.is_overdue),
        blocked=sum(1 for row in rows if row.task.status == WbsTask.Status.BLOCKED),
        in_progress=sum(1 for row in rows if row.task.status == WbsTask.Status.IN_PROGRESS),
        done=sum(1 for row in rows if row.task.status == WbsTask.Status.DONE),
        filters=filters,
    )


def _build_row(task: WbsTask, today, soon) -> TaskRow:
    """期限の判定。完了済みを超過扱いすると常時赤くなり、警告の意味が失われる。"""

    unfinished = task.status not in (WbsTask.Status.DONE, WbsTask.Status.ARCHIVED)
    has_due = task.planned_end is not None

    return TaskRow(
        task=task,
        is_overdue=bool(has_due and unfinished and task.planned_end < today),
        is_due_soon=bool(has_due and unfinished and today <= task.planned_end <= soon),
    )
