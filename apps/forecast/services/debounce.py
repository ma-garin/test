"""AH-06: 再計算のデバウンスと、重大イベントの即時処理。

同じ機能・WBS へ短時間に何度もイベントが来たとき、そのたびに全部を計算し直すと
履歴も通知も雑音になる。まとめて 1 回にする。

ただし重大不具合と期日の悪化は待たせない。「まとめる」ことが目的ではなく、
「PMO が気づくのが遅れない」ことが目的である。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.forecast.models.signals import SignalClassification
from apps.projects.models import Severity

#: まとめる時間の窓。この間に来た通常イベントは 1 回の再計算にする。
DEBOUNCE_SECONDS = 300

#: 待たせない分類。重大不具合の起票と日程更新は、その場で再計算する。
IMMEDIATE_CLASSIFICATIONS = (
    SignalClassification.DEFECT_REPORTED,
    SignalClassification.SCHEDULE_UPDATE,
)

#: 待たせない重大度。
IMMEDIATE_SEVERITIES = (Severity.CRITICAL, Severity.HIGH)


@dataclass(frozen=True)
class RecomputeDecision:
    """再計算をいま実行するか、まとめて後で実行するか。"""

    run_now: bool
    due_at: datetime | None
    reason: str

    @property
    def is_deferred(self) -> bool:
        return not self.run_now


def decide_recompute(
    *,
    classification: str,
    severity: str | None = None,
    last_recomputed_at: datetime | None = None,
    now: datetime | None = None,
) -> RecomputeDecision:
    """再計算の実行可否を決める。

    判断は 3 つだけにする。重大なら即時、直近に計算済みならまとめる、それ以外は即時。
    条件を増やすと「なぜ今計算されたのか」を説明できなくなる。
    """

    moment = now or timezone.now()

    if classification in IMMEDIATE_CLASSIFICATIONS or severity in IMMEDIATE_SEVERITIES:
        return RecomputeDecision(
            run_now=True, due_at=None, reason="重大イベントのため即時に再計算します。"
        )

    if last_recomputed_at is None:
        return RecomputeDecision(run_now=True, due_at=None, reason="初回の再計算です。")

    elapsed = (moment - last_recomputed_at).total_seconds()
    if elapsed < DEBOUNCE_SECONDS:
        due = last_recomputed_at + timedelta(seconds=DEBOUNCE_SECONDS)
        return RecomputeDecision(
            run_now=False,
            due_at=due,
            reason=(
                f"直近 {int(elapsed)} 秒以内に再計算済みのため、"
                f"{DEBOUNCE_SECONDS} 秒の窓でまとめます。"
            ),
        )

    return RecomputeDecision(run_now=True, due_at=None, reason="デバウンスの窓を過ぎています。")
