"""LDF-08: 日次・週次報告と、通知候補。

PMO が夜に情報を集め直さずに済むよう、「前回確認後の変化」だけを集める。
ただし下書きは下書きである。AI の推定を確定事項として載せない。

不変条件:
- 前回報告以降の差分・判断待ち・未確認事項・根拠リンクを必ず含める。
- 算定不能を「変化なし」と混ぜない。
- 通知は、予測の悪化・確信度の低下・鮮度切れのときだけ作る。通常更新で乱発しない。
- **外部への送信はしない。** ここが作るのは下書きと通知候補までである。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.forecast.models.snapshots import Confidence, ForecastSnapshot, Horizon
from apps.forecast.services.freshness import ProjectFreshness
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState

#: 日次報告が見る既定の期間。
DAILY_WINDOW = timedelta(days=1)

#: 週次報告が見る既定の期間。
WEEKLY_WINDOW = timedelta(days=7)

#: 1 案件あたりに並べる変化の上限。読み切れない量を出すと、報告として使われない。
#: 打ち切った件数は必ず表示する（黙って切ると「これで全部」と誤読される）。
CHANGE_DISPLAY_LIMIT = 10


@dataclass(frozen=True)
class ForecastChange:
    """前回からの変化 1 件。悪化・改善・算定不能化を区別する。"""

    snapshot: ForecastSnapshot

    @property
    def target_name(self) -> str:
        """対象名だけだと、3 時点の予測が同じ行に見える。地平まで含める。"""

        return f"{self.snapshot.target}（{self.snapshot.get_horizon_display()}）"

    @property
    def became_undeterminable(self) -> bool:
        previous = self.snapshot.previous
        return self.snapshot.is_undeterminable and (
            previous is not None and not previous.is_undeterminable
        )

    @property
    def delta(self) -> int | None:
        return self.snapshot.variance_from_previous

    @property
    def direction(self) -> str:
        if self.became_undeterminable:
            return "算定不能化"
        if self.delta is None:
            return "新規"
        if self.delta > 0:
            return "悪化"
        if self.delta < 0:
            return "改善"
        return "変化なし"

    @property
    def is_notable(self) -> bool:
        """報告に載せる価値があるか。変化なしは載せない。"""

        return self.direction in ("悪化", "改善", "算定不能化", "新規")

    @property
    def needs_notification(self) -> bool:
        """通知する価値があるか。改善では通知しない。"""

        return self.direction in ("悪化", "算定不能化")

    def describe(self) -> str:
        if self.became_undeterminable:
            reasons = "、".join(self.snapshot.missing_input_labels()) or "入力不足"
            return f"{self.target_name}: 算定不能になりました（{reasons}）。"
        if self.delta and self.delta > 0:
            return f"{self.target_name}: {self.delta} 営業日 悪化（{self.snapshot.display_date}）。"
        if self.delta and self.delta < 0:
            return f"{self.target_name}: {abs(self.delta)} 営業日 改善（{self.snapshot.display_date}）。"
        return f"{self.target_name}: {self.snapshot.display_date}（{self.snapshot.summary}）"


@dataclass(frozen=True)
class ReportDraft:
    """報告の下書き。確認済みの情報と未確認事項を分けて持つ。"""

    project: object
    since: datetime
    until: datetime
    changes: tuple[ForecastChange, ...] = ()
    undeterminable: tuple[ForecastSnapshot, ...] = ()
    pending_reviews: tuple[ForecastSnapshot, ...] = ()
    unconfirmed_links: tuple[WorkLink, ...] = ()
    freshness_note: str = ""

    @property
    def notable_changes(self) -> tuple[ForecastChange, ...]:
        """報告に載せる変化。悪化を先に、上限まで。"""

        notable = [change for change in self.changes if change.is_notable]
        notable.sort(key=lambda change: (0 if change.needs_notification else 1))
        return tuple(notable[:CHANGE_DISPLAY_LIMIT])

    @property
    def hidden_change_count(self) -> int:
        """上限で省いた件数。0 でないときは画面と文面の両方に出す。"""

        notable = sum(1 for change in self.changes if change.is_notable)
        return max(0, notable - CHANGE_DISPLAY_LIMIT)

    @property
    def notifications(self) -> tuple[ForecastChange, ...]:
        return tuple(change for change in self.changes if change.needs_notification)

    @property
    def is_quiet(self) -> bool:
        """報告すべき変化が無い状態。空の報告と、変化なしの報告を区別する。"""

        return not self.notable_changes and not self.undeterminable

    def as_text(self) -> str:
        """共有前に人が編集できるテキスト。確定していないことを明示する。"""

        lines = [
            f"# {self.project.code} {self.project.name} 着地予測の報告（下書き）",
            f"対象期間: {self.since:%Y/%m/%d %H:%M} 〜 {self.until:%Y/%m/%d %H:%M}",
            "",
            "この文面は下書きです。未確認事項を確認してから共有してください。",
            "",
            "## 前回からの変化",
        ]
        _add_section(
            lines,
            [f"- {change.describe()}" for change in self.notable_changes],
            empty="- 変化はありません。",
        )
        if self.hidden_change_count:
            lines.append(f"- ほか {self.hidden_change_count} 件（画面で全件を確認してください）")

        lines += ["", "## 算定不能の項目"]
        _add_section(
            lines,
            [
                f"- {snapshot.target}（{snapshot.get_horizon_display()}）: "
                f"{'、'.join(snapshot.missing_input_labels())}"
                for snapshot in self.undeterminable
            ],
            empty="- ありません。",
        )

        lines += ["", "## 未確認事項（確定していません）"]
        _add_section(
            lines,
            [
                f"- 未確認の関連: {link.from_object} → {link.to_object}（{link.provenance}）"
                for link in self.unconfirmed_links
            ],
            empty="- ありません。",
        )

        lines += ["", "## 判断待ちの予測"]
        _add_section(
            lines,
            [
                f"- {snapshot.target} {snapshot.get_horizon_display()}: {snapshot.display_date}"
                for snapshot in self.pending_reviews
            ],
            empty="- ありません。",
        )

        if self.freshness_note:
            lines += ["", "## 情報の鮮度", f"- {self.freshness_note}"]

        return "\n".join(lines)


def build_report(project, *, window: timedelta = DAILY_WINDOW, now=None) -> ReportDraft:
    """前回確認後の変化を集めた報告の下書きを作る。"""

    until = now or timezone.now()
    since = until - window

    snapshots = list(
        ForecastSnapshot.objects.filter(
            project=project, as_of__gte=since, as_of__lte=until
        ).select_related("previous")
    )
    changes = tuple(ForecastChange(snapshot=snapshot) for snapshot in snapshots)

    undeterminable = tuple(
        snapshot
        for snapshot in _latest_per_target(project)
        if snapshot.confidence == Confidence.UNKNOWN
    )
    pending = tuple(
        snapshot
        for snapshot in snapshots
        if not snapshot.reviews.exists() and snapshot.horizon == Horizon.MILESTONE
    )
    unconfirmed = tuple(
        WorkLink.objects.filter(project=project, state=LinkState.CANDIDATE)[:20]
    )

    return ReportDraft(
        project=project,
        since=since,
        until=until,
        changes=changes,
        undeterminable=undeterminable,
        pending_reviews=pending,
        unconfirmed_links=unconfirmed,
        freshness_note=ProjectFreshness.for_project(project, until).describe(),
    )


def _add_section(lines: list[str], rows: list[str], *, empty: str) -> None:
    """行があればそれを、無ければ「ありません」を書く。

    `list.extend(...) or lines.append(...)` と書くと、`extend` が None を返すため
    行があるときにも「ありません」が併記される。
    """

    lines.extend(rows) if rows else lines.append(empty)


def _latest_per_target(project) -> tuple[ForecastSnapshot, ...]:
    """対象・地平ごとの最新スナップショット。過去の算定不能を今の状態にしない。"""

    latest: dict = {}
    for snapshot in ForecastSnapshot.objects.filter(project=project).order_by("as_of"):
        latest[(snapshot.target_content_type_id, snapshot.target_object_id, snapshot.horizon)] = (
            snapshot
        )
    return tuple(latest.values())
