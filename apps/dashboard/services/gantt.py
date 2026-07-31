"""タスク一覧のガント表示で使う描画位置の計算。

日付から棒の位置を求める処理をテンプレートに置くと、境界（期間が 1 日しかない、
開始日と終了日が逆、期間が未設定）の扱いが画面ごとにずれる。ここで
「期間全体の何 % の位置に、何 % の長さで置くか」まで確定させ、テンプレートは
その値を style に埋めるだけにする。

表とガントで見える対象が食い違うと不整合になるため、入力は一覧表と同じ
`TaskRow`（絞り込み済み）をそのまま受け取る。色の規則も `TaskRow.tone` を
再利用し、二重定義しない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from apps.dashboard.services.tasks import TaskRow
from apps.projects.models import WbsTask

#: 棒の最小幅（%）。1 日だけのタスクが長期間の中に埋もれて消えるのを防ぐ。
MIN_BAR_WIDTH = 0.6

#: 目盛りの本数。多すぎると日付が重なって読めない。
TICK_COUNT = 5


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """0〜100 の範囲へ寄せる。範囲外の style は描画結果が予測できないため。"""

    return max(low, min(high, value))


@dataclass(frozen=True)
class GanttTick:
    """期間の目盛り。日付が読めないと棒の長さが解釈できない。"""

    label: str
    left: float


@dataclass(frozen=True)
class GanttBar:
    """1 タスクの棒。left / width はいずれも期間全体に対する %。"""

    row: TaskRow
    left: float
    width: float

    @property
    def task(self) -> WbsTask:
        return self.row.task

    @property
    def tone(self) -> str:
        """期限の強調色。一覧表のバッジと同じ規則を使う。"""

        return self.row.tone

    @property
    def progress_width(self) -> float:
        """棒の内側に塗る割合（棒の長さに対する %）。

        計画（棒全体）に対して実績がどこまで進んだかを、棒の中の塗りで示す。
        """

        return _clamp(float(self.task.progress_percent or 0))


@dataclass(frozen=True)
class GanttGroup:
    """案件ごとの棒の束。案件をまたいで並べると読み手が文脈を失う。"""

    code: str
    name: str
    bars: tuple[GanttBar, ...]


@dataclass(frozen=True)
class GanttChart:
    """ガント表示が必要とするものすべて。"""

    groups: tuple[GanttGroup, ...]
    undated: tuple[TaskRow, ...]
    start: date | None
    end: date | None
    days: int
    today_left: float | None
    ticks: tuple[GanttTick, ...]

    @property
    def has_bars(self) -> bool:
        return bool(self.groups)

    @property
    def has_today(self) -> bool:
        """今日が期間内にあるときだけ縦線を引く。範囲外に引くと嘘になる。"""

        return self.today_left is not None

    @property
    def is_empty(self) -> bool:
        return not self.groups and not self.undated

    @property
    def bar_count(self) -> int:
        return sum(len(group.bars) for group in self.groups)


def build_gantt_chart(rows: Sequence[TaskRow], today: date) -> GanttChart:
    """一覧表と同じ行から、ガント表示用の構造を作る。"""

    dated: list[TaskRow] = []
    undated: list[TaskRow] = []

    for row in rows:
        if row.task.planned_start and row.task.planned_end:
            dated.append(row)
        else:
            # 棒を描けないタスクを黙って落とすと、ガントに出ないタスクの
            # 存在に気づけない。別枠へ回して必ず可視化する。
            undated.append(row)

    if not dated:
        return GanttChart(
            groups=(),
            undated=tuple(undated),
            start=None,
            end=None,
            days=0,
            today_left=None,
            ticks=(),
        )

    start = min(row.task.planned_start for row in dated)
    end = max(_end_of(row.task) for row in dated)

    # 全タスクが同じ日に集中していると幅が 0 になり除算で落ちる。最低 1 日とみなす。
    days = max((end - start).days + 1, 1)

    return GanttChart(
        groups=_group_by_project(dated, start, days),
        undated=tuple(undated),
        start=start,
        end=end,
        days=days,
        today_left=_today_left(today, start, end, days),
        ticks=_build_ticks(start, days),
    )


def _end_of(task: WbsTask) -> date:
    """終了日。開始日より前の終了日は入力誤りなので、開始日へ丸めて幅を保つ。"""

    return max(task.planned_end, task.planned_start)


def _group_by_project(rows: Sequence[TaskRow], start: date, days: int) -> tuple[GanttGroup, ...]:
    """案件ごとにまとめる。入力の並び順（期限が近い順）は保つ。"""

    buckets: dict[int, list[GanttBar]] = {}
    labels: dict[int, tuple[str, str]] = {}

    for row in rows:
        project = row.task.project
        buckets.setdefault(project.pk, []).append(_build_bar(row, start, days))
        labels.setdefault(project.pk, (project.code, project.name))

    return tuple(
        GanttGroup(code=labels[pk][0], name=labels[pk][1], bars=tuple(bars))
        for pk, bars in buckets.items()
    )


def _build_bar(row: TaskRow, start: date, days: int) -> GanttBar:
    """開始日を左位置に、期間の日数を幅に変換する。"""

    task_start = row.task.planned_start
    task_end = _end_of(row.task)

    left = _clamp((task_start - start).days / days * 100)
    width = _clamp(((task_end - task_start).days + 1) / days * 100, MIN_BAR_WIDTH)

    # 右端をはみ出すと隣の列へ重なるため、残り幅に収める。
    width = min(width, 100 - left)

    return GanttBar(row=row, left=round(left, 2), width=round(max(width, 0.0), 2))


def _today_left(today: date, start: date, end: date, days: int) -> float | None:
    if not start <= today <= end:
        return None

    return round(_clamp((today - start).days / days * 100), 2)


def _tick_plan(days: int) -> tuple[int, str]:
    """期間の長さから、目盛りの本数と日付の書式を決める。

    本数を固定にすると、1 年を超えた計画で「5/30」のような月日だけが 5 つ並び、
    どの年の話か分からなくなる。逆に短期間で本数を増やすと日付が重なる。
    期間に応じて、読める粒度へ落とす。
    """

    if days > 730:
        return 6, "%Y/%-m"

    if days > 365:
        return 6, "%y/%-m"

    if days > 180:
        return 7, "%-m/%-d"

    if days > 60:
        return 6, "%-m/%-d"

    return TICK_COUNT, "%-m/%-d"


def _build_ticks(start: date, days: int) -> tuple[GanttTick, ...]:
    """期間を等分した目盛り。1 日しかない場合は先頭だけ出す。"""

    if days <= 1:
        return (GanttTick(label=start.strftime("%-m/%-d"), left=0.0),)

    count, fmt = _tick_plan(days)
    ticks: list[GanttTick] = []

    for index in range(count):
        ratio = index / (count - 1)
        offset = round(ratio * (days - 1))
        ticks.append(
            GanttTick(
                label=(start.fromordinal(start.toordinal() + offset)).strftime(fmt),
                left=round(_clamp(offset / days * 100), 2),
            )
        )

    return tuple(ticks)
