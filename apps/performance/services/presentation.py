"""画面に出す数字の絞り込み。

計数は売上・粗利・利益の3系列 × 計画・実績・差 で、素直に並べると1つの表が
10列を超える。ラインマネージャーが最初に知りたいのは「計画に対してどうか」の
1点なので、**画面では常に1指標だけを表に出し、指標は上のタブで切り替える**。
残り2つを消すのではなく、見る順番を決めている。

ここは表示のための整形だけを行い、集計そのものは `aggregation` に置く。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from apps.performance.services.aggregation import Amounts, Comparison, rate, tone_for

#: 切り替えられる指標。既定は売上（計画の起点になる数字）。
METRICS: tuple[tuple[str, str], ...] = (
    ("revenue", "売上"),
    ("gross_profit", "粗利"),
    ("operating_profit", "利益"),
)

METRIC_KEYS = tuple(key for key, _ in METRICS)
DEFAULT_METRIC = "revenue"

METRIC_LABELS = dict(METRICS)


def metric_from(request) -> str:
    """`?metric=` の指標。不正な値は既定へ落とす（404 にはしない）。"""

    requested = request.GET.get("metric", "")

    return requested if requested in METRIC_KEYS else DEFAULT_METRIC


def metric_tabs(current: str) -> list[dict]:
    return [
        {"key": key, "label": label, "is_current": key == current} for key, label in METRICS
    ]


def value_of(amounts: Amounts, metric: str) -> Decimal:
    return getattr(amounts, metric)


@dataclass(frozen=True)
class Row:
    """表の1行。計画・実績・差・達成率だけを持つ。"""

    label: str
    plan: Decimal
    actual: Decimal
    url: str = ""
    note: str = ""

    @property
    def diff(self) -> Decimal:
        return self.actual - self.plan

    @property
    def achievement(self) -> Decimal | None:
        return rate(self.actual, self.plan)

    @property
    def tone(self) -> str:
        return tone_for(self.achievement)

    @property
    def status_label(self) -> str:
        return {"g": "達成", "a": "あと少し", "r": "未達", "n": "計画なし"}[self.tone]

    @property
    def is_behind(self) -> bool:
        return self.tone == "r"

    @property
    def needs_attention(self) -> bool:
        """未達と「あと少し」。両方出さないと、90%台の組織が誰の目にも触れない。"""

        return self.tone in ("r", "a")


def row_from(label: str, comparison: Comparison, metric: str, url: str = "", note: str = "") -> Row:
    return Row(
        label=label,
        plan=value_of(comparison.plan, metric),
        actual=value_of(comparison.actual, metric),
        url=url,
        note=note,
    )


@dataclass(frozen=True)
class Headline:
    """画面の一番上に置く1つの結論。"""

    metric_label: str
    plan: Decimal
    actual: Decimal
    profit_rate: Decimal | None
    gross_margin_rate: Decimal | None

    @property
    def diff(self) -> Decimal:
        return self.actual - self.plan

    @property
    def achievement(self) -> Decimal | None:
        return rate(self.actual, self.plan)

    @property
    def tone(self) -> str:
        return tone_for(self.achievement)

    @property
    def status_label(self) -> str:
        return {"g": "計画どおり", "a": "あと少し", "r": "未達", "n": "計画なし"}[self.tone]

    @property
    def sentence(self) -> str:
        """数字を読まなくても状況が分かる一文。"""

        if self.achievement is None:
            return f"{self.metric_label}の計画が登録されていません。"

        if self.tone == "g":
            return f"{self.metric_label}は計画に対して {self.achievement:.0f}% です。計画を上回っています。"

        return (
            f"{self.metric_label}は計画に対して {self.achievement:.0f}%、"
            f"{abs(self.diff):,.0f} 円足りていません。"
        )


def headline_from(comparison: Comparison, metric: str) -> Headline:
    return Headline(
        metric_label=METRIC_LABELS[metric],
        plan=value_of(comparison.plan, metric),
        actual=value_of(comparison.actual, metric),
        profit_rate=comparison.actual.profit_rate,
        gross_margin_rate=comparison.actual.gross_margin_rate,
    )
