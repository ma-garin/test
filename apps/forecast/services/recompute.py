"""LDF-03 / AH-05: 計算結果をスナップショットとして残す。

予測は上書きしない。時点ごとに残し、前回との差と根拠をたどれるようにする。
ただし「変わっていないのに毎回作る」と、履歴も通知も雑音になる。値が同じなら
新しいスナップショットを作らない（同一イベントの再送で重複させないため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.forecast.models.snapshots import ForecastEvidence, ForecastSnapshot
from apps.forecast.services.engine import TargetForecast, compute_project_forecast
from apps.forecast.services.freshness import ProjectFreshness

#: 予測が「変わった」と見なすフィールド。要約や根拠の差だけでは履歴を増やさない。
SIGNIFICANT_FIELDS = (
    "forecast_date",
    "variance_business_days",
    "confidence",
    "baseline_date",
)


@dataclass(frozen=True)
class RecomputeResult:
    """再計算の結果。作成した数と、変化しなかった数を分けて返す。"""

    created: tuple[ForecastSnapshot, ...] = ()
    unchanged: int = 0

    @property
    def worsened(self) -> tuple[ForecastSnapshot, ...]:
        """前回より悪化したものだけ。通知はここだけを対象にする。"""

        return tuple(
            snapshot
            for snapshot in self.created
            if (snapshot.variance_from_previous or 0) > 0
        )

    @property
    def became_undeterminable(self) -> tuple[ForecastSnapshot, ...]:
        return tuple(
            snapshot
            for snapshot in self.created
            if snapshot.is_undeterminable
            and snapshot.previous is not None
            and not snapshot.previous.is_undeterminable
        )


@transaction.atomic
def recompute_project(project, as_of: date | None = None, *, evidence=()) -> RecomputeResult:
    """案件の予測を再計算し、変化した分だけスナップショットにする。"""

    as_of_date = as_of or timezone.localdate()
    # 鮮度は必ず評価する。古い情報のまま自信のある予測を出し続けないため（AH-06）。
    computed = compute_project_forecast(
        project, as_of_date, freshness=ProjectFreshness.for_project(project)
    )
    now = timezone.now()

    created: list[ForecastSnapshot] = []
    unchanged = 0

    for target in computed.targets:
        previous = ForecastSnapshot.objects.latest_for(target.target, target.horizon)
        if previous is not None and not _has_changed(previous, target):
            unchanged += 1
            continue

        snapshot = ForecastSnapshot(
            project=project,
            target=target.target,
            as_of=now,
            horizon=target.horizon,
            baseline_date=target.baseline_date,
            forecast_date=target.forecast_date,
            variance_business_days=target.variance_business_days,
            confidence=target.confidence,
            missing_inputs=list(target.missing_inputs),
            summary=target.summary[:300],
            previous=previous,
        )
        snapshot.save()
        _attach_evidence(snapshot, evidence)
        created.append(snapshot)

    return RecomputeResult(created=tuple(created), unchanged=unchanged)


def _has_changed(previous: ForecastSnapshot, target: TargetForecast) -> bool:
    current = {
        "forecast_date": target.forecast_date,
        "variance_business_days": target.variance_business_days,
        "confidence": target.confidence,
        "baseline_date": target.baseline_date,
    }
    return any(getattr(previous, name) != current[name] for name in SIGNIFICANT_FIELDS)


def _attach_evidence(snapshot: ForecastSnapshot, evidence) -> None:
    """根拠を結び付ける。使えない Signal は「不使用」として残し、黙って捨てない。"""

    for signal in evidence:
        role = (
            ForecastEvidence.Role.USED
            if signal.is_usable_as_evidence
            else ForecastEvidence.Role.UNUSED_CANDIDATE
        )
        ForecastEvidence.objects.get_or_create(
            snapshot=snapshot, signal=signal, defaults={"role": role}
        )
