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


#: 金額の表示単位。1億を超える数字が並ぶ画面では、円のままだと桁が読めない。
#: (キー, ラベル, 除数, 小数桁)。単位を上げた分だけ小数を残さないと、
#: 「180.36 百万円」が「180」になって 36万円が消える。
UNITS: tuple[tuple[str, str, int, int], ...] = (
    ("yen", "円", 1, 0),
    ("thousand", "千円", 1_000, 0),
    ("million", "百万円", 1_000_000, 1),
)

UNIT_KEYS = tuple(key for key, _, _, _ in UNITS)
DEFAULT_UNIT = "yen"
UNIT_LABELS = {key: label for key, label, _, _ in UNITS}
UNIT_DIVISORS = {key: divisor for key, _, divisor, _ in UNITS}
UNIT_DECIMALS = {key: decimals for key, _, _, decimals in UNITS}


def unit_decimals(unit: str) -> int:
    """その単位で残す小数桁。テンプレートの floatformat へ渡す。"""

    return UNIT_DECIMALS.get(unit, 0)


def unit_from(request) -> str:
    """`?unit=` の表示単位。不正な値は既定（円）へ落とす。"""

    requested = request.GET.get("unit", "")

    return requested if requested in UNIT_KEYS else DEFAULT_UNIT


def unit_tabs(current: str) -> list[dict]:
    return [
        {"key": key, "label": label, "is_current": key == current} for key, label, _, _ in UNITS
    ]


def scale(value: Decimal | None, unit: str) -> Decimal | None:
    """金額を表示単位へ換算する。率や件数には使わない。

    切り捨てず、割った値をそのまま返す。丸めは表示側（floatformat）で行う。
    ここで丸めると、合計と内訳が一致しない表ができる。
    """

    if value is None:
        return None

    divisor = UNIT_DIVISORS.get(unit, 1)

    return value if divisor == 1 else Decimal(value) / divisor


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
    unit: str = DEFAULT_UNIT

    @property
    def plan_display(self) -> Decimal | None:
        return scale(self.plan, self.unit)

    @property
    def actual_display(self) -> Decimal | None:
        return scale(self.actual, self.unit)

    @property
    def diff_display(self) -> Decimal | None:
        return scale(self.diff, self.unit)

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


def row_from(
    label: str, comparison: Comparison, metric: str,
    url: str = "", note: str = "", unit: str = DEFAULT_UNIT,
) -> Row:
    return Row(
        label=label,
        plan=value_of(comparison.plan, metric),
        actual=value_of(comparison.actual, metric),
        url=url,
        note=note,
        unit=unit,
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


@dataclass(frozen=True)
class SummaryRow:
    """計数サマリ表の1行。金額の行と率の行を同じ形で扱う。

    率の行は「達成率」「前年比」を出さない。率どうしの比（13.05 ÷ 13.12）は
    意味を持たず、見るべきは差（ポイント）だから。金額と率で列の意味を変えず、
    率のときだけ比率欄を空けることで、読み違いを防ぐ。

    前年実績（`prior_actual`）は年度に前年度が登録されているときだけ埋まる。
    無ければ全ての前年関連プロパティが None になり、テンプレート側は「未確認」
    として「—」を出す（0% と誤読される数字を作らない）。
    """

    label: str
    plan: Decimal | None
    actual: Decimal | None
    prior_actual: Decimal | None = None
    is_rate: bool = False
    #: 金額の表示単位。率の行では使わない（率は単位を持たない）。
    unit: str = DEFAULT_UNIT

    def _scaled(self, value: Decimal | None) -> Decimal | None:
        """表示単位へ換算する。率の行はそのまま返す。"""

        return value if self.is_rate else scale(value, self.unit)

    @property
    def plan_display(self) -> Decimal | None:
        return self._scaled(self.plan)

    @property
    def actual_display(self) -> Decimal | None:
        return self._scaled(self.actual)

    @property
    def prior_display(self) -> Decimal | None:
        return self._scaled(self.prior_actual)

    @property
    def diff_display(self) -> Decimal | None:
        return self._scaled(self.diff)

    @property
    def diff(self) -> Decimal | None:
        if self.plan is None or self.actual is None:
            return None

        return self.actual - self.plan

    @property
    def achievement(self) -> Decimal | None:
        if self.is_rate or self.plan is None or self.actual is None:
            return None

        return rate(self.actual, self.plan)

    @property
    def tone(self) -> str:
        if self.is_rate:
            if self.diff is None:
                return "n"

            return "g" if self.diff >= 0 else "r"

        return tone_for(self.achievement)

    @property
    def yoy_diff(self) -> Decimal | None:
        """前年同期との差。率の行はポイント差、金額の行は増減額。"""

        if self.actual is None or self.prior_actual is None:
            return None

        return self.actual - self.prior_actual

    @property
    def yoy_rate(self) -> Decimal | None:
        """前年同期比（%）。率の行では比を出さない（達成率と同じ理由）。"""

        if self.is_rate or self.actual is None or self.prior_actual is None:
            return None

        return rate(self.actual, self.prior_actual)

    @property
    def yoy_tone(self) -> str:
        """前年比のトーン。達成率と違い「あと少し」は無く、増減の2値。"""

        if self.yoy_diff is None:
            return "n"

        return "g" if self.yoy_diff >= 0 else "r"


def summary_rows(
    comparison: Comparison, prior: Amounts | None = None, unit: str = DEFAULT_UNIT
) -> list[SummaryRow]:
    """売上・粗利・利益と、粗利率・利益率。経営会議で最初に見る並び。

    `prior` は前年同期の実績（`FiscalYear.previous` が無ければ None）。
    `unit` は金額の表示単位。率の行には効かせない。
    """

    plan, actual = comparison.plan, comparison.actual

    def prior_of(name: str) -> Decimal | None:
        return getattr(prior, name) if prior is not None else None

    return [
        SummaryRow("売上", plan.revenue, actual.revenue, prior_of("revenue"), unit=unit),
        SummaryRow("粗利", plan.gross_profit, actual.gross_profit, prior_of("gross_profit"), unit=unit),
        SummaryRow(
            "粗利率",
            plan.gross_margin_rate,
            actual.gross_margin_rate,
            prior.gross_margin_rate if prior is not None else None,
            is_rate=True,
        ),
        SummaryRow(
            "利益", plan.operating_profit, actual.operating_profit,
            prior_of("operating_profit"), unit=unit,
        ),
        SummaryRow(
            "利益率",
            plan.profit_rate,
            actual.profit_rate,
            prior.profit_rate if prior is not None else None,
            is_rate=True,
        ),
    ]


@dataclass(frozen=True)
class OrgLine:
    """組織別一覧の1行。3指標すべての実績と対計画比を持つ。

    指標を1つに絞ると「売上は届いたが利益が出ていない」組織を見落とす。
    一覧では3指標を並べ、掘るときに詳細画面へ移る。
    """

    label: str
    url: str
    note: str
    revenue: Row
    gross_profit: Row
    operating_profit: Row
    profit_rate: Decimal | None

    @property
    def tone(self) -> str:
        """行の判定は利益を基準にする。売上だけ達成した行を「達成」にしない。"""

        tones = (self.revenue.tone, self.operating_profit.tone)

        for level in ("r", "a", "n"):
            if level in tones:
                return level

        return "g"

    @property
    def status_label(self) -> str:
        return {"g": "達成", "a": "あと少し", "r": "未達", "n": "計画なし"}[self.tone]

    @property
    def needs_attention(self) -> bool:
        return self.tone in ("r", "a")

    @property
    def worst_achievement(self) -> Decimal:
        """並べ替え用。低いほうを行の代表値にする。"""

        values = [
            value
            for value in (self.revenue.achievement, self.operating_profit.achievement)
            if value is not None
        ]

        return min(values) if values else Decimal("0")


def org_line(
    label: str, comparison: Comparison, url: str = "", note: str = "", unit: str = DEFAULT_UNIT
) -> OrgLine:
    return OrgLine(
        label=label,
        url=url,
        note=note,
        revenue=row_from(label, comparison, "revenue", unit=unit),
        gross_profit=row_from(label, comparison, "gross_profit", unit=unit),
        operating_profit=row_from(label, comparison, "operating_profit", unit=unit),
        profit_rate=comparison.actual.profit_rate,
    )
