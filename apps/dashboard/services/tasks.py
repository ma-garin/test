"""タスク一覧画面の集計。

一覧をそのまま出すだけでは「どれから見ればよいか」が分からないため、
期限超過・ブロック中の件数を先に出し、行ごとに強調の色を決めておく。
色の判定を画面側の `{% if %}` に散らすと基準がずれるので、ここで確定させる。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.db.models import Count, F, Max, Q, QuerySet
from django.utils import timezone

from apps.projects.models import Priority, WbsTask

#: 期限接近とみなす日数。
DUE_SOON_DAYS = 7

#: 「今すぐ確認」に出す最大件数。読み切れる数に抑え、続きは通常の表で見せる。
ATTENTION_LIMIT = 3

#: JT-01: 既存の GET 条件だけで危険な対象へ 1 クリックで到達するためのビュー。
#: 新しい条件を足さないので、`条件をクリア` で必ず全件へ戻れる。
QUICK_VIEWS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("blocked", "ブロック中", {"status": WbsTask.Status.BLOCKED}),
    ("overdue", "期限超過", {"due": "overdue"}),
    ("due_soon", "7日以内", {"due": "due_soon"}),
    ("not_started", "未着手", {"status": WbsTask.Status.NOT_STARTED}),
)


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

    @property
    def row_tone(self) -> str:
        """JT-03: 行そのものの強調。完了行は強調しない（警告の意味が薄れるため）。"""

        if self.task.status == WbsTask.Status.BLOCKED:
            return "row-blocked"

        return "row-overdue" if self.is_overdue else ""


@dataclass(frozen=True)
class QuickView:
    """JT-01: 既存の絞り込み条件へ 1 クリックで移動するリンク。"""

    key: str
    label: str
    query: str
    is_active: bool


@dataclass(frozen=True)
class AttentionItem:
    """JT-04: 表より先に処理すべき 1 件。理由を持たない候補は出さない。"""

    task: WbsTask
    reason: str
    tone: str
    #: 並び順。小さいほど先に出す（0=ブロック中、1=期限超過、2=期限接近）。
    rank: int


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
    latest_updated_at: datetime | None
    attention: tuple[AttentionItem, ...] = ()

    @property
    def quick_views(self) -> tuple[QuickView, ...]:
        """JT-01: 現在の条件と一致するものを選択中として返す。"""

        return tuple(
            QuickView(
                key=key,
                label=label,
                query=urlencode(params),
                is_active=all(getattr(self.filters, name) == value for name, value in params.items()),
            )
            for key, label, params in QUICK_VIEWS
        )

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
    def done_percent(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0


def build_task_board(
    tasks: QuerySet[WbsTask],
    filters: TaskFilters,
    display_tasks: Iterable[WbsTask] | None = None,
) -> TaskBoard:
    """絞り込み済みのタスクから画面表示用の構造を作る。

    `tasks` は集計用の全件、`display_tasks` は表示する 1 ページ分。
    集計を表示行から数えると、ページを送るたびに「期限超過 12 件」が
    「期限超過 0 件」に変わってしまう。同じ絞り込み条件なら何ページ目でも
    同じ数字が出るよう、件数は必ず全件から取る。
    """

    today = timezone.localdate()
    soon = today + timedelta(days=DUE_SOON_DAYS)
    visible = tasks if display_tasks is None else display_tasks

    rows = tuple(_build_row(task, today, soon) for task in visible)

    return TaskBoard(
        rows=rows,
        filters=filters,
        attention=_build_attention(tasks, today, soon),
        **_summarize(tasks, today),
    )


def _build_attention(
    tasks: QuerySet[WbsTask], today: date, soon: date
) -> tuple[AttentionItem, ...]:
    """JT-04: 絞り込み後の全件から、最も危ない数件を理由つきで取り出す。

    ページ内の行から選ぶと 2 ページ目で「今すぐ確認」が消える。件数と同じく
    全件から取る。理由を作れない候補（期限もブロックもない）は出さない。
    """

    unfinished = ~Q(status__in=(WbsTask.Status.DONE, WbsTask.Status.ARCHIVED))
    candidates = (
        tasks.filter(unfinished)
        .filter(Q(status=WbsTask.Status.BLOCKED) | Q(planned_end__lte=soon))
        .order_by(F("planned_end").asc(nulls_last=True), "project__code", "wbs_code")[
            : ATTENTION_LIMIT * 4
        ]
    )

    items = [_attention_item(task, today) for task in candidates]
    items.sort(key=lambda item: (item.rank, item.task.planned_end or date.max))
    return tuple(items[:ATTENTION_LIMIT])


def _attention_item(task: WbsTask, today: date) -> AttentionItem:
    """理由の文面。日数を推測せず、期限が無い場合は無いと書く。"""

    if task.status == WbsTask.Status.BLOCKED:
        holder = task.ball_holder or task.owner or "担当未設定"
        return AttentionItem(task=task, reason=f"ブロック中／次に動くのは {holder}", tone="r", rank=0)

    if task.planned_end and task.planned_end < today:
        days = (today - task.planned_end).days
        return AttentionItem(task=task, reason=f"期限を {days} 日超過", tone="r", rank=1)

    days = (task.planned_end - today).days if task.planned_end else None
    label = "期限まで 0 日" if days == 0 else f"期限まで {days} 日"
    return AttentionItem(task=task, reason=label, tone="a", rank=2)


def _summarize(tasks: QuerySet[WbsTask], today: date) -> dict[str, int | datetime | None]:
    """件数の集計。Python で数えると全件を読み込むことになるので DB 側で数える。"""

    unfinished = ~Q(status__in=(WbsTask.Status.DONE, WbsTask.Status.ARCHIVED))

    return tasks.aggregate(
        total=Count("pk"),
        overdue=Count("pk", filter=Q(planned_end__lt=today) & unfinished),
        blocked=Count("pk", filter=Q(status=WbsTask.Status.BLOCKED)),
        in_progress=Count("pk", filter=Q(status=WbsTask.Status.IN_PROGRESS)),
        done=Count("pk", filter=Q(status=WbsTask.Status.DONE)),
        latest_updated_at=Max("updated_at"),
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
