"""出来高管理（EVM）と完了予測。

進捗率だけでは「いつ終わるか」に答えられない。残り 20% が 1 日なのか 1 ヶ月なのかは、
工数を持たないと分からない。PMO が最初に問われるのはそこなので、
計画工数・実績工数から PV / EV / AC を出し、SPI / CPI と完了予測日まで繋ぐ。

用語（PMBOK の定義に従う）:

- **PV**（Planned Value）… 基準日までに完了しているはずの計画工数
- **EV**（Earned Value）… 実際に完了した分の計画工数（＝出来高）
- **AC**（Actual Cost）… 実際にかかった工数
- **SPI** = EV / PV … 1.0 未満なら計画より遅れている
- **CPI** = EV / AC … 1.0 未満なら計画より多く工数を使っている
- **EAC** = BAC / CPI … 現在の効率が続いた場合の総工数見込み

工数が入っていないタスクは計算から除外し、**除外した件数を必ず返す**。
母数が分からない指標は判断に使えないため。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import QuerySet

from apps.projects.models import Project, WbsTask

#: 完了予測に使う直近の実績期間（日）。短すぎると1日の変動に振られる。
VELOCITY_WINDOW_DAYS = 14

#: SPI / CPI がこの値を下回ったら警告扱い。
WARN_INDEX = Decimal("0.90")

#: SPI / CPI がこの値を下回ったら重大扱い。
CRITICAL_INDEX = Decimal("0.75")


@dataclass(frozen=True)
class EarnedValue:
    """1 案件の出来高。

    工数が未入力のタスクがあると指標が実態とずれるため、
    対象件数と除外件数を必ず持たせる。
    """

    project: Project
    planned_value: Decimal
    earned_value: Decimal
    actual_cost: Decimal
    budget_at_completion: Decimal
    counted_tasks: int
    skipped_tasks: int
    remaining_hours: Decimal
    daily_velocity: Decimal | None
    as_of: date

    @property
    def has_data(self) -> bool:
        """指標を語ってよいか。工数が1件も無ければ語らない。"""

        return self.counted_tasks > 0 and self.budget_at_completion > 0

    @property
    def coverage_percent(self) -> int:
        """工数が入力されているタスクの割合。低いほど指標の信頼度が下がる。"""

        total = self.counted_tasks + self.skipped_tasks

        return round(100 * self.counted_tasks / total) if total else 0

    @property
    def schedule_index(self) -> Decimal | None:
        """SPI。1.0 未満なら遅れている。"""

        if not self.has_data or self.planned_value <= 0:
            return None

        return (self.earned_value / self.planned_value).quantize(Decimal("0.01"))

    @property
    def cost_index(self) -> Decimal | None:
        """CPI。1.0 未満なら計画より工数を使っている。"""

        if not self.has_data or self.actual_cost <= 0:
            return None

        return (self.earned_value / self.actual_cost).quantize(Decimal("0.01"))

    @property
    def estimate_at_completion(self) -> Decimal | None:
        """EAC。現在の効率が続いた場合の総工数見込み。"""

        cpi = self.cost_index

        if cpi is None or cpi <= 0:
            return None

        return (self.budget_at_completion / cpi).quantize(Decimal("0.1"))

    @property
    def variance_at_completion(self) -> Decimal | None:
        """VAC。計画総工数との差。負なら超過見込み。"""

        eac = self.estimate_at_completion

        return None if eac is None else (self.budget_at_completion - eac).quantize(Decimal("0.1"))

    @property
    def forecast_end_date(self) -> date | None:
        """完了予測日。

        直近の消化速度（人時/日）で残工数を割る。速度が出せないときは
        推測で日付を出さない。**根拠のない予測日は、あるより悪い。**
        """

        if not self.has_data or self.daily_velocity is None or self.daily_velocity <= 0:
            return None

        days = int((self.remaining_hours / self.daily_velocity).to_integral_value(rounding="ROUND_CEILING"))

        return self.as_of + timedelta(days=max(days, 0))

    @property
    def forecast_note(self) -> str:
        """予測できない理由。空欄のままにせず、必ず理由を返す。"""

        if not self.has_data:
            return "計画工数が入力されていないため算出できません"

        if self.daily_velocity is None or self.daily_velocity <= 0:
            return f"直近{VELOCITY_WINDOW_DAYS}日の実績が無いため、消化速度を出せません"

        return ""

    @property
    def tone(self) -> str:
        """表示色。SPI と CPI の悪い方に合わせる。"""

        indexes = [i for i in (self.schedule_index, self.cost_index) if i is not None]

        if not indexes:
            return "n"

        worst = min(indexes)

        if worst < CRITICAL_INDEX:
            return "r"

        return "a" if worst < WARN_INDEX else "g"

    @property
    def schedule_label(self) -> str:
        spi = self.schedule_index

        if spi is None:
            return "判定不能"

        if spi < CRITICAL_INDEX:
            return "大幅な遅れ"

        if spi < WARN_INDEX:
            return "遅れ"

        return "計画通り" if spi < Decimal("1.10") else "前倒し"


def build_earned_value(project: Project, as_of: date) -> EarnedValue:
    """1 案件の出来高を算出する。

    PV は「基準日までに終わっているはずの工数」。計画終了日が基準日以前の
    タスクは全額、期間中のタスクは経過割合で按分する。按分しないと、
    長いタスクが1つあるだけで PV が階段状に跳ねて SPI が意味を失う。
    """

    tasks = list(WbsTask.objects.filter(project=project).exclude(status=WbsTask.Status.ARCHIVED))

    planned = Decimal("0")
    earned = Decimal("0")
    actual = Decimal("0")
    budget = Decimal("0")
    counted = 0
    skipped = 0

    for task in tasks:
        hours = task.planned_hours

        if hours is None or hours <= 0:
            skipped += 1
            continue

        counted += 1
        budget += hours
        planned += _planned_value(task, hours, as_of)
        earned += hours * (Decimal(task.progress_percent) / Decimal("100"))
        actual += task.actual_hours or Decimal("0")

    return EarnedValue(
        project=project,
        planned_value=planned.quantize(Decimal("0.1")),
        earned_value=earned.quantize(Decimal("0.1")),
        actual_cost=actual.quantize(Decimal("0.1")),
        budget_at_completion=budget.quantize(Decimal("0.1")),
        counted_tasks=counted,
        skipped_tasks=skipped,
        remaining_hours=(budget - earned).quantize(Decimal("0.1")),
        daily_velocity=_daily_velocity(tasks, as_of),
        as_of=as_of,
    )


def build_portfolio(projects: QuerySet[Project], as_of: date) -> list[EarnedValue]:
    """案件ごとの出来高。遅れている順に並べる。"""

    rows = [build_earned_value(project, as_of) for project in projects]

    return sorted(rows, key=lambda row: row.schedule_index or Decimal("9"))


def _planned_value(task: WbsTask, hours: Decimal, as_of: date) -> Decimal:
    """このタスクが基準日までに終わっているはずの工数。"""

    start = task.planned_start
    end = task.planned_end

    if end is None:
        # 期限が無いタスクは「まだ終わっていなくてよい」と扱う。
        return Decimal("0")

    if end <= as_of:
        return hours

    if start is None or start >= as_of:
        return Decimal("0")

    # 期間中は経過割合で按分する。
    span = (end - start).days or 1
    elapsed = (as_of - start).days

    return (hours * Decimal(elapsed) / Decimal(span)).quantize(Decimal("0.1"))


def _daily_velocity(tasks: list[WbsTask], as_of: date) -> Decimal | None:
    """直近の消化速度（人時/日）。

    完了したタスクの計画工数を、その期間の日数で割る。実績工数ではなく
    計画工数を使うのは、EV と単位を揃えるため（残工数も計画工数ベース）。
    """

    since = as_of - timedelta(days=VELOCITY_WINDOW_DAYS)
    done_hours = Decimal("0")

    for task in tasks:
        if task.status != WbsTask.Status.DONE or task.planned_hours is None:
            continue

        finished = task.actual_end or task.planned_end

        if finished is None or finished < since or finished > as_of:
            continue

        done_hours += task.planned_hours

    if done_hours <= 0:
        return None

    return (done_hours / Decimal(VELOCITY_WINDOW_DAYS)).quantize(Decimal("0.01"))
