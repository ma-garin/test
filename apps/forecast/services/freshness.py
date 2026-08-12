"""AH-06: 情報の鮮度。

「リアルタイム」を画面を開いた瞬間の見た目ではなく、予測に使った情報の鮮度として
定義する。連携が止まっている案件で、古い情報のまま自信のある予測を出し続けない。

`docs/改善に.md`:「連携が失敗・停止・許可外で、合意した鮮度を超えた場合は
『着地予測の信頼度低下』として明示する。安全側にもっともらしい予測を出し続けない。」
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Max
from django.utils import timezone

from apps.forecast.models.signals import Signal, SignalSource

#: 情報源ごとの鮮度目標（時間）。顧客との合意で変える前提の既定値。
#: 会話は流量が多いので短く、計画データは日次で足りる。
FRESHNESS_HOURS: dict[str, int] = {
    SignalSource.SLACK: 24,
    SignalSource.JIRA: 24,
    SignalSource.REDMINE: 24,
    SignalSource.GIT: 48,
    SignalSource.CI: 48,
    SignalSource.TEST_MANAGEMENT: 48,
    SignalSource.CONFLUENCE: 168,
    SignalSource.MANUAL: 168,
    SignalSource.INTERNAL: 168,
}


@dataclass(frozen=True)
class SourceFreshness:
    """1 情報源の鮮度。取得が一度も無い場合と、古い場合を区別する。"""

    source: str
    last_seen_at: datetime | None
    threshold_hours: int

    @property
    def source_label(self) -> str:
        return dict(SignalSource.choices).get(self.source, self.source)

    @property
    def is_missing(self) -> bool:
        """一度も取得していない。古いのとは意味が違うので分けて扱う。"""

        return self.last_seen_at is None

    def is_stale(self, now: datetime) -> bool:
        if self.last_seen_at is None:
            return False
        return now - self.last_seen_at > timedelta(hours=self.threshold_hours)

    def age_hours(self, now: datetime) -> int | None:
        if self.last_seen_at is None:
            return None
        return int((now - self.last_seen_at).total_seconds() // 3600)


@dataclass(frozen=True)
class ProjectFreshness:
    """案件 1 件分の鮮度。予測の確信度を下げるかどうかの根拠になる。"""

    sources: tuple[SourceFreshness, ...] = ()
    checked_at: datetime | None = None

    @classmethod
    def for_project(cls, project, now: datetime | None = None) -> ProjectFreshness:
        moment = now or timezone.now()
        latest = dict(
            Signal.objects.filter(project=project, is_revoked=False)
            .values_list("source")
            .annotate(last=Max("occurred_at"))
        )
        sources = tuple(
            SourceFreshness(
                source=source,
                last_seen_at=latest.get(source),
                threshold_hours=hours,
            )
            for source, hours in FRESHNESS_HOURS.items()
            if source in latest
        )
        return cls(sources=sources, checked_at=moment)

    @property
    def stale_sources(self) -> tuple[SourceFreshness, ...]:
        if self.checked_at is None:
            return ()
        return tuple(item for item in self.sources if item.is_stale(self.checked_at))

    @property
    def has_any_signal(self) -> bool:
        return bool(self.sources)

    @property
    def is_degraded(self) -> bool:
        """鮮度切れがあり、予測の確信度を下げるべき状態か。"""

        return bool(self.stale_sources)

    def describe(self) -> str:
        """画面・通知にそのまま出せる一文。数字だけを通知しない。"""

        if not self.has_any_signal:
            return "外部からの情報がまだ届いていません。連携設定または手動登録が必要です。"
        if not self.is_degraded:
            return "すべての情報源が鮮度目標の範囲内です。"

        parts = [
            f"{item.source_label}（{item.age_hours(self.checked_at)}時間前 / 目標{item.threshold_hours}時間）"
            for item in self.stale_sources
        ]
        return "鮮度切れ: " + "、".join(parts) + "。予測の確信度を下げています。"
