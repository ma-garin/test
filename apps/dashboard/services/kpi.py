"""KPI・効果測定の集計。

仕様書の効果測定指標（レポート作業時間・赤字率・事実誤認件数・予兆検知の先行日数）を、
指標ごとの最新計測値で基準値と比べる。

指標によって「増えたら良い」「減ったら良い」が逆になるため、改善率の符号は
指標ごとに反転させる。ここを間違えると、悪化を改善として表示してしまう。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import QuerySet

from apps.dashboard.models import KpiMeasurement

#: 値が小さいほど良い指標。これ以外は大きいほど良いものとして扱う。
LOWER_IS_BETTER = (
    KpiMeasurement.Kind.REPORT_HOURS,
    KpiMeasurement.Kind.CORRECTION_RATE,
    KpiMeasurement.Kind.FACT_ERROR_COUNT,
)


@dataclass(frozen=True)
class KpiRow:
    """指標1件の表示用データ。"""

    measurement: KpiMeasurement

    @property
    def lower_is_better(self) -> bool:
        return self.measurement.kind in LOWER_IS_BETTER

    @property
    def improvement_percent(self) -> int | None:
        """基準値からの改善率。基準値が無い／0 なら算出しない。"""

        baseline = self.measurement.baseline_value
        actual = self.measurement.actual_value

        if baseline is None or baseline == 0:
            return None

        ratio = (baseline - actual) / baseline * 100

        if not self.lower_is_better:
            ratio = -ratio

        return int(ratio)

    @property
    def target_achieved(self) -> bool | None:
        """目標に到達しているか。目標未設定なら判定しない。"""

        target = self.measurement.target_value

        if target is None:
            return None

        if self.lower_is_better:
            return self.measurement.actual_value <= target

        return self.measurement.actual_value >= target

    @property
    def tone(self) -> str:
        """表示色。改善=g / 悪化=r / 判定不能=n。"""

        improvement = self.improvement_percent

        if improvement is None:
            return "n"

        if improvement >= 10:
            return "g"

        if improvement < 0:
            return "r"

        return "a"


@dataclass(frozen=True)
class KpiReport:
    rows: tuple[KpiRow, ...]
    measured_count: int

    @property
    def improved_count(self) -> int:
        return len([row for row in self.rows if (row.improvement_percent or 0) > 0])

    @property
    def achieved_count(self) -> int:
        return len([row for row in self.rows if row.target_achieved])

    @property
    def missing_kinds(self) -> list[str]:
        """計測がまだ無い指標。空欄で放置せず「未計測」として明示する。"""

        measured = {row.measurement.kind for row in self.rows}

        return [label for value, label in KpiMeasurement.Kind.choices if value not in measured]


def build_kpi_report(measurements: QuerySet[KpiMeasurement]) -> KpiReport:
    """指標ごとに最新の計測値だけを残す。

    `kpi_measurements_for()` が kind 昇順・計測日降順で返すので、
    kind が変わった最初の1件が最新値になる。
    """

    latest: list[KpiMeasurement] = []
    seen: set[str] = set()
    total = 0

    for measurement in measurements:
        total += 1

        if measurement.kind in seen:
            continue

        seen.add(measurement.kind)
        latest.append(measurement)

    return KpiReport(
        rows=tuple(KpiRow(measurement=measurement) for measurement in latest),
        measured_count=total,
    )


def format_value(value: Decimal | None) -> str:
    """末尾の 0 を落とした表示用文字列。テンプレートの floatformat では桁が揃わない。"""

    if value is None:
        return "—"

    return f"{value.normalize():f}"


#: 代替指標の基準値をとる過去日数。
BASELINE_DAYS = 30


@dataclass(frozen=True)
class DerivedRow:
    """KPI 実績が未登録のときに既存データから作る代替行。

    実測と混ぜると「測った値」と「計算した値」の区別がつかなくなるため、
    KpiRow とは別の型にしている。
    """

    label: str
    baseline: float
    actual: float
    unit: str
    note: str

    @property
    def improvement_point(self) -> float:
        """基準値からの変化量（ポイント）。いずれも大きいほど良い指標のみ扱う。"""

        return round(self.actual - self.baseline, 1)

    @property
    def improvement_percent(self) -> int | None:
        if not self.baseline:
            return None

        return round(100 * self.improvement_point / self.baseline)

    @property
    def tone(self) -> str:
        if self.improvement_point > 0:
            return "g"

        return "r" if self.improvement_point < 0 else "a"

    @property
    def bar_percent(self) -> int:
        return max(0, min(100, round(self.actual)))


def build_derived_rows(projects) -> tuple[DerivedRow, ...]:
    """既存の WBS・課題・不具合から基準値／実績／改善率を組み立てる。

    基準値には「BASELINE_DAYS 日前時点で既に完了していた分」を使う。
    実測の基準値が無い環境でも、良くなっているのか悪くなっているのかを言えるようにする。
    循環 import を避けるため、案件系モデルはここで遅延 import する。
    """

    from datetime import timedelta

    from django.utils import timezone

    from apps.projects.models import Defect, Issue, WbsTask

    threshold = timezone.localdate() - timedelta(days=BASELINE_DAYS)
    tasks = WbsTask.objects.filter(project__in=projects)
    issues = Issue.objects.filter(project__in=projects)
    defects = Defect.objects.filter(project__in=projects)
    resolved = (Issue.Status.RESOLVED, Issue.Status.CLOSED)

    specs = (
        ("タスク完了率", tasks.count(), tasks.filter(status=WbsTask.Status.DONE),
         {"actual_end__lte": threshold}),
        ("課題解決率", issues.count(), issues.filter(status__in=resolved),
         {"resolved_at__date__lte": threshold}),
        ("不具合クローズ率", defects.count(), defects.filter(status=Defect.Status.CLOSED),
         {"closed_on__lte": threshold}),
    )

    return tuple(
        DerivedRow(
            label=label,
            baseline=_rate(total, done.filter(**past).count()),
            actual=_rate(total, done.count()),
            unit="%",
            note=f"母数 {total}件 / 基準値は{BASELINE_DAYS}日前時点",
        )
        for label, total, done, past in specs
    )


def _rate(total: int, count: int) -> float:
    """割合。母数 0 のときは 0% とし、行そのものは消さない（未整備が見えなくなるため）。"""

    return round(100 * count / total, 1) if total else 0.0
