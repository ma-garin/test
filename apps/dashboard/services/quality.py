"""品質リアルタイム管理画面の集計。

品質指標は時系列で溜まるため、画面に出すのは指標キーごとの最新値だけにする。
「消化率」は QualityMetric に登録があればその値を、無ければ不具合の
クローズ率（バグ消化率）で代替する。指標が未整備の環境でも
画面が空にならないようにするため。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import QuerySet

from apps.projects.models import Defect, QualityMetric, Severity

#: 消化率として扱う指標キー。旧実装のシード値に合わせている。
CONSUMPTION_KEYS = ("test_consumption_rate", "test_execution_rate", "consumption_rate")


@dataclass(frozen=True)
class MetricRow:
    metric: QualityMetric

    @property
    def tone(self) -> str:
        """品質ゲートの判定色。閾値未設定なら判定しない。"""

        passes = self.metric.passes_gate

        if passes is None:
            return "n"

        return "g" if passes else "r"

    @property
    def gate_label(self) -> str:
        passes = self.metric.passes_gate

        if passes is None:
            return "閾値未設定"

        return "ゲート合格" if passes else "ゲート未達"


@dataclass(frozen=True)
class CountRow:
    """状態別・重大度別の件数。バーの幅も一緒に持たせる。"""

    label: str
    count: int
    percent: int
    tone: str


@dataclass(frozen=True)
class QualityReport:
    metric_rows: tuple[MetricRow, ...]
    status_rows: tuple[CountRow, ...]
    severity_rows: tuple[CountRow, ...]
    total_defects: int
    closed_defects: int
    open_critical: int
    consumption_percent: int
    consumption_source: str

    @property
    def closed_percent(self) -> int:
        return round(100 * self.closed_defects / self.total_defects) if self.total_defects else 0

    @property
    def gate_failures(self) -> int:
        return sum(1 for row in self.metric_rows if row.metric.passes_gate is False)


def build_quality_report(
    metrics: QuerySet[QualityMetric], defects: QuerySet[Defect]
) -> QualityReport:
    """品質指標の最新値と不具合の集計をまとめる。"""

    metric_rows = tuple(MetricRow(metric=metric) for metric in _latest_metrics(metrics))
    materialized = list(defects)
    total = len(materialized)
    closed = sum(1 for defect in materialized if defect.status == Defect.Status.CLOSED)
    consumption, source = _consumption(metric_rows, total, closed)

    return QualityReport(
        metric_rows=metric_rows,
        status_rows=_count_rows(materialized, Defect.Status.choices, "status", total),
        severity_rows=_count_rows(materialized, Severity.choices, "severity", total),
        total_defects=total,
        closed_defects=closed,
        open_critical=sum(
            1
            for defect in materialized
            if defect.severity in (Severity.HIGH, Severity.CRITICAL)
            and defect.status != Defect.Status.CLOSED
        ),
        consumption_percent=consumption,
        consumption_source=source,
    )


def _latest_metrics(metrics: QuerySet[QualityMetric]) -> list[QualityMetric]:
    """案件×指標キーごとに最新の 1 件だけ残す。

    ウィンドウ関数を使わないのは、SQLite を含む全環境で同じ結果にするため。
    件数は指標の種類数どまりなので Python 側で畳んで問題ない。
    """

    seen: set[tuple[int, str]] = set()
    latest: list[QualityMetric] = []

    for metric in metrics:
        key = (metric.project_id, metric.metric_key)

        if key in seen:
            continue

        seen.add(key)
        latest.append(metric)

    return latest


def _count_rows(defects: list[Defect], choices, attribute: str, total: int) -> tuple[CountRow, ...]:
    """選択肢の並び順で件数を並べる。0 件の状態も出して全体像を崩さない。"""

    tones = {"new": "r", "analyzing": "a", "fixing": "b", "verifying": "p", "closed": "g"}
    severity_tones = {"critical": "r", "high": "r", "medium": "a", "low": "n"}
    tones.update(severity_tones)

    rows = []

    for value, label in choices:
        count = sum(1 for defect in defects if getattr(defect, attribute) == value)
        rows.append(
            CountRow(
                label=label,
                count=count,
                percent=round(100 * count / total) if total else 0,
                tone=tones.get(value, "n"),
            )
        )

    return tuple(rows)


def _as_percent(metric) -> float:
    """指標の値を % へ揃える。

    同じ「消化率」でも 0〜1 の比率で登録する現場と 0〜100 の百分率で登録する
    現場がある。単位が入っていればそれに従い、無ければ 1 以下を比率とみなす。
    無変換のままだと、比率で登録した現場では常に 0% と表示されていた。
    """

    value = float(metric.value)
    unit = (metric.unit or "").strip()

    if unit in ("%", "percent", "パーセント"):
        return value

    if unit in ("ratio", "比率", "割合"):
        return value * 100

    return value * 100 if value <= 1 else value


def _consumption(metric_rows: tuple[MetricRow, ...], total: int, closed: int) -> tuple[int, str]:
    """消化率と、その値がどこから来たかの説明。"""

    measured = [row for row in metric_rows if row.metric.metric_key in CONSUMPTION_KEYS]

    if measured:
        # 案件ごとに 1 件ずつ入るので、複数案件を横断しているときに先頭 1 件を
        # 全体の値として出すと、他の案件の消化状況が消える。平均を取る。
        percents = [_as_percent(row.metric) for row in measured]
        average = round(sum(percents) / len(percents))
        label = measured[0].metric.metric_label or measured[0].metric.metric_key
        note = f"{label}（実測）"

        if len(measured) > 1:
            note = f"{label}（実測 {len(measured)} 案件の平均）"

        return average, note

    if total:
        return round(100 * closed / total), "不具合クローズ率で代替（消化率の指標が未登録）"

    return 0, "算出に使えるデータがありません"
