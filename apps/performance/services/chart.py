"""月次の計画対比グラフ。

SVG の座標をサーバー側で算出し、テンプレートは描くだけにする。描画ライブラリを
入れないのは、この1枚のためにフロントの依存とビルドを増やしたくないから。

**エンコード**

- 実績 … 棒（青 #2563eb）。金額の大小はそのまま棒の高さで読む。
- 計画 … 折れ線（橙 #d97706）。基準線なので、実績の上を横切る形になる。

2色は CVD 分離を検証済み（protan/tritan ともに ΔE > 29）。凡例に加えて
「棒＝実績／線＝計画」と形も変えているので、色だけに頼っていない。
下の月次表が同じ数字の表形式なので、色が読めない環境でも情報は失われない。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: 図の内部座標。実寸はテンプレート側の CSS（width:100%）で決める。
WIDTH = 760
# 1画面へ収めるため、縦は必要最小限に留める。棒の高さの差が読めれば足りる。
HEIGHT = 150
PAD_LEFT = 58
PAD_RIGHT = 10
PAD_TOP = 12
PAD_BOTTOM = 24

MILLION = Decimal("1000000")
THOUSAND = Decimal("1000")


def _axis_unit(top: Decimal) -> tuple[Decimal, str]:
    """軸の目盛りに使う単位。値の大きさから決める。

    表示単位（円・千円・百万円）とは別に持つ。円を選んでいても
    軸へ9桁を並べると目盛りが読めないため、軸は常に読める桁へ落とす。
    """

    if top >= MILLION:
        return MILLION, "百万円"

    if top >= THOUSAND:
        return THOUSAND, "千円"

    return Decimal(1), "円"


@dataclass(frozen=True)
class Bar:
    x: float
    y: float
    width: float
    height: float
    label: str
    actual: Decimal
    plan: Decimal
    plan_y: float
    plan_x: float


@dataclass(frozen=True)
class Chart:
    bars: list[Bar]
    ticks: list[dict]
    plan_path: str
    baseline: float
    #: 目盛りの単位名。注記へ出す。
    axis_unit: str = "円"
    width: int = WIDTH
    height: int = HEIGHT

    @property
    def has_data(self) -> bool:
        return any(bar.actual or bar.plan for bar in self.bars)


#: 目盛り上限の候補（1桁の刻み）。棒の頭が図の上端に近づく値を選ぶ。
NICE_FACTORS = (
    Decimal("1"),
    Decimal("1.25"),
    Decimal("1.5"),
    Decimal("2"),
    Decimal("2.5"),
    Decimal("3"),
    Decimal("4"),
    Decimal("5"),
    Decimal("6"),
    Decimal("8"),
    Decimal("10"),
)


def _nice_max(value: Decimal) -> Decimal:
    """目盛りの上限。

    刻みを粗く取りすぎると（3,200万に対して上限5,000万など）棒が図の下半分に
    へばりつき、月ごとの差が読めなくなる。実測値の少し上で止まる刻みを選ぶ。
    """

    if value <= 0:
        return Decimal("1")

    unit = Decimal(10) ** (len(str(int(value))) - 1)

    for factor in NICE_FACTORS:
        candidate = unit * factor

        if value <= candidate:
            return candidate

    return unit * 10


def monthly_chart(rows, metric: str) -> Chart:
    """月次の実績（棒）と計画（線）。`rows` は `aggregation.MonthlyRow` の並び。"""

    values = []

    for row in rows:
        values.append(getattr(row.actual, metric))
        values.append(getattr(row.plan, metric))

    top = _nice_max(max(values) if values else Decimal("0"))
    plot_height = HEIGHT - PAD_TOP - PAD_BOTTOM
    plot_width = WIDTH - PAD_LEFT - PAD_RIGHT
    baseline = PAD_TOP + plot_height
    slot = plot_width / max(len(rows), 1)
    bar_width = max(slot * 0.52, 6)

    def y_of(value: Decimal) -> float:
        ratio = float(value) / float(top) if top else 0.0

        return baseline - ratio * plot_height

    bars: list[Bar] = []
    points: list[str] = []

    for index, row in enumerate(rows):
        actual = getattr(row.actual, metric)
        plan = getattr(row.plan, metric)
        center = PAD_LEFT + slot * index + slot / 2
        top_y = y_of(actual)
        plan_y = y_of(plan)

        bars.append(
            Bar(
                x=center - bar_width / 2,
                y=top_y,
                width=bar_width,
                height=max(baseline - top_y, 0),
                label=f"{row.month:%-m月}",
                actual=actual,
                plan=plan,
                plan_y=plan_y,
                plan_x=center,
            )
        )
        points.append(f"{center:.1f},{plan_y:.1f}")

    divisor, axis_unit = _axis_unit(top)
    ticks = []

    for step in range(3):
        value = top / 2 * step
        scaled = value / divisor
        # 端数を切り捨てて「12」と出すと、実際の目盛り（12.5）と食い違う。
        label = f"{scaled:,.0f}" if scaled == scaled.to_integral_value() else f"{scaled:,.1f}"
        ticks.append({"y": round(y_of(value), 1), "label": label})

    return Chart(
        bars=bars,
        ticks=ticks,
        plan_path=" ".join(points),
        baseline=round(baseline, 1),
        axis_unit=axis_unit,
    )
