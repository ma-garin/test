"""GE-02: 有向依存・マイルストーン紐付け・勤務日カレンダー。

`WbsTask.related_tasks` は対称の関連であり、「どちらが先か」「何営業日空くか」を
表せない。着地予測は前方向の計算なので、方向とラグを持つ依存が要る。

不変条件:
- 依存は同じ案件内でだけ張れる。
- 循環依存は保存できない（保存できると予測が無限ループするか、黙って誤る）。
- マイルストーンの必須タスクは、計画日が近いことではなく人の確認で決める。
- 暦日を営業日として扱わない。勤務日は案件ごとのカレンダーで決める。
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.projects.models import ProjectScopedModel

#: 依存をたどる深さの上限。案件内でも、事故的な長大連鎖で計算が止まらないようにする。
MAX_DEPENDENCY_DEPTH = 200


class DependencyCycleError(ValidationError):
    """循環依存。予測を止め、経路を示すために専用の例外にする。"""

    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path
        super().__init__(f"循環依存です: {' → '.join(path)}")


class TaskDependency(ProjectScopedModel):
    """WBS 間の有向依存。P0 では Finish-to-Start を主に使う。"""

    class Kind(models.TextChoices):
        FINISH_TO_START = "fs", "先行の完了後に開始"
        START_TO_START = "ss", "先行の開始後に開始"
        FINISH_TO_FINISH = "ff", "先行の完了後に完了"
        START_TO_FINISH = "sf", "先行の開始後に完了"

    predecessor = models.ForeignKey(
        "projects.WbsTask",
        verbose_name="先行タスク",
        on_delete=models.CASCADE,
        related_name="successor_links",
    )
    successor = models.ForeignKey(
        "projects.WbsTask",
        verbose_name="後続タスク",
        on_delete=models.CASCADE,
        related_name="predecessor_links",
    )
    dependency_type = models.CharField(
        "依存種別", max_length=4, choices=Kind.choices, default=Kind.FINISH_TO_START
    )
    lag_business_days = models.IntegerField(
        "ラグ（営業日）",
        default=0,
        help_text="先行の完了から後続の開始まで空ける営業日数。負値は前倒し許容。",
    )
    confirmed_by = models.ForeignKey(
        "accounts.User",
        verbose_name="確認者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_dependencies",
    )
    confirmed_at = models.DateTimeField("確認日時", null=True, blank=True)

    @property
    def is_confirmed(self) -> bool:
        """人が確認したか。未確認の依存・紐付けは確信度を下げる根拠になる。"""

        return self.confirmed_by_id is not None

    def confirm(self, user):
        """人の確定。確認者と時刻を残し、AI が確定させたように見せない。"""

        self.confirmed_by = user
        self.confirmed_at = timezone.now()
        self.save()
        return self

    class Meta:
        verbose_name = "WBS依存"
        verbose_name_plural = "WBS依存"
        ordering = ["project__code", "predecessor__wbs_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["predecessor", "successor"], name="graph_dependency_unique_pair"
            ),
            models.CheckConstraint(
                condition=~models.Q(predecessor=models.F("successor")),
                name="graph_dependency_no_self_loop",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "predecessor"]),
            models.Index(fields=["project", "successor"]),
        ]

    def __str__(self) -> str:
        return f"{self.predecessor.wbs_code} → {self.successor.wbs_code}"

    def clean(self) -> None:
        super().clean()
        if self.predecessor_id == self.successor_id:
            raise ValidationError("自分自身へは依存できません。")

        if self.predecessor.project_id != self.successor.project_id:
            raise ValidationError(
                "案件をまたぐ WBS 依存は作れません。案件間の依存は WorkLink で明示します。"
            )

        if not self.project_id:
            self.project = self.predecessor.project

        self._assert_no_cycle()

    def _assert_no_cycle(self) -> None:
        """後続方向へたどって先行タスクへ戻れるなら循環。

        保存前に検出する。入ってしまうと、予測の前方向計算が止まらなくなる。
        """

        if self.predecessor_id == self.successor_id:
            return

        path = _find_path(self.successor_id, self.predecessor_id, exclude_pk=self.pk)
        if path is not None:
            raise DependencyCycleError(_codes_for((self.predecessor_id, *path)))

    def save(self, *args, **kwargs):
        # 循環は専用例外で呼び出し側へ伝える。`full_clean` を通すと
        # ただの ValidationError に丸められ、経路を出せなくなる。
        self._assert_no_cycle()
        self.full_clean(exclude=["project"])
        return super().save(*args, **kwargs)


class MilestoneTaskLink(ProjectScopedModel):
    """マイルストーンと WBS の紐付け。必須かどうかは人が決める。

    「計画終了日がマイルストーン直前だから必須」と推定すると、無関係なタスクの
    遅延がそのまま期日の遅延として表示される。
    """

    milestone = models.ForeignKey(
        "projects.Milestone",
        verbose_name="マイルストーン",
        on_delete=models.CASCADE,
        related_name="task_links",
    )
    task = models.ForeignKey(
        "projects.WbsTask",
        verbose_name="WBSタスク",
        on_delete=models.CASCADE,
        related_name="milestone_links",
    )
    is_required = models.BooleanField(
        "必須", default=True, help_text="False なら着地予測の計算対象にしない。"
    )
    confirmed_by = models.ForeignKey(
        "accounts.User",
        verbose_name="確認者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_milestone_links",
    )
    confirmed_at = models.DateTimeField("確認日時", null=True, blank=True)

    @property
    def is_confirmed(self) -> bool:
        """人が確認したか。未確認の依存・紐付けは確信度を下げる根拠になる。"""

        return self.confirmed_by_id is not None

    def confirm(self, user):
        """人の確定。確認者と時刻を残し、AI が確定させたように見せない。"""

        self.confirmed_by = user
        self.confirmed_at = timezone.now()
        self.save()
        return self

    class Meta:
        verbose_name = "マイルストーン紐付け"
        verbose_name_plural = "マイルストーン紐付け"
        ordering = ["milestone__planned_date", "task__wbs_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["milestone", "task"], name="graph_milestone_task_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.milestone.name} ← {self.task.wbs_code}"

    def clean(self) -> None:
        super().clean()
        if self.milestone.project_id != self.task.project_id:
            raise ValidationError("マイルストーンとタスクの案件が一致しません。")

        if not self.project_id:
            self.project = self.milestone.project

    def save(self, *args, **kwargs):
        self.full_clean(exclude=["project"])
        return super().save(*args, **kwargs)


class WorkingCalendar(ProjectScopedModel):
    """案件の勤務日。暦日で日数を数えないために持つ。"""

    #: 既定の勤務曜日（月〜金）。`datetime.date.weekday()` と同じ 0=月曜。
    DEFAULT_WORKING_WEEKDAYS = "0,1,2,3,4"

    working_weekdays = models.CharField(
        "勤務曜日",
        max_length=16,
        default=DEFAULT_WORKING_WEEKDAYS,
        help_text="0=月曜。カンマ区切り。休日・特例日は CalendarDay で上書きする。",
    )
    name = models.CharField("名称", max_length=120, default="標準カレンダー")

    class Meta:
        verbose_name = "勤務カレンダー"
        verbose_name_plural = "勤務カレンダー"
        constraints = [
            models.UniqueConstraint(fields=["project"], name="graph_calendar_one_per_project"),
        ]

    def __str__(self) -> str:
        return f"{self.project.code} {self.name}"

    @property
    def weekday_numbers(self) -> frozenset[int]:
        return frozenset(
            int(part) for part in self.working_weekdays.split(",") if part.strip().isdigit()
        )

    def clean(self) -> None:
        super().clean()
        if not self.weekday_numbers:
            raise ValidationError("勤務曜日が 1 日もないカレンダーは作れません。")
        if any(day > 6 for day in self.weekday_numbers):
            raise ValidationError("勤務曜日は 0〜6 で指定してください。")

    def save(self, *args, **kwargs):
        # project は呼び出し側が必ず渡すので除外しない。除外すると
        # 「1 案件 1 カレンダー」の制約が検証されず、DB エラーになって初めて分かる。
        self.full_clean()
        return super().save(*args, **kwargs)


class CalendarDay(models.Model):
    """カレンダーの特例日。祝日、臨時稼働日、リリース凍結を同じ表で持つ。"""

    class Kind(models.TextChoices):
        HOLIDAY = "holiday", "休業日"
        WORKDAY = "workday", "臨時稼働日"
        FREEZE = "freeze", "リリース凍結"

    calendar = models.ForeignKey(
        WorkingCalendar, verbose_name="カレンダー", on_delete=models.CASCADE, related_name="days"
    )
    date = models.DateField("日付")
    kind = models.CharField("種別", max_length=16, choices=Kind.choices)
    label = models.CharField("名称", max_length=120, blank=True)

    class Meta:
        verbose_name = "カレンダー特例日"
        verbose_name_plural = "カレンダー特例日"
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "date", "kind"], name="graph_calendar_day_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.get_kind_display()}"


def _find_path(start_id, goal_id, *, exclude_pk=None) -> tuple | None:
    """`start` から後続方向へたどって `goal` に着く経路を返す。無ければ None。"""

    edges = TaskDependency.objects.exclude(pk=exclude_pk).values_list(
        "predecessor_id", "successor_id"
    )
    successors: dict = {}
    for predecessor_id, successor_id in edges:
        successors.setdefault(predecessor_id, []).append(successor_id)

    stack: list[tuple] = [(start_id,)]
    seen = set()
    while stack:
        path = stack.pop()
        node = path[-1]
        if node == goal_id:
            return path
        if node in seen or len(path) > MAX_DEPENDENCY_DEPTH:
            continue
        seen.add(node)
        stack.extend((*path, nxt) for nxt in successors.get(node, ()))
    return None


def _codes_for(task_ids: tuple) -> tuple[str, ...]:
    """循環経路を WBS コードで示す。ID の羅列では修正できない。"""

    from apps.projects.models import WbsTask

    codes = dict(WbsTask.objects.filter(pk__in=task_ids).values_list("pk", "wbs_code"))
    return tuple(codes.get(task_id, str(task_id)) for task_id in task_ids)
