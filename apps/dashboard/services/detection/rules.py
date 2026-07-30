"""検知しきい値の読み出し。

しきい値を関数越しに取るのは 2 つの理由による。

1. 現場ごとに基準が違うため、コードへ数値を埋め込まない（`settings.DETECTION_RULES`）
2. 設定を一部だけ差し替えたテストを書けるようにする（既定値とマージして返す）
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from apps.projects.models import WbsTask

#: 進行中と見なさないタスク状態。波及計算・停滞判定の対象から外す。
FINISHED_TASK_STATUSES: tuple[str, ...] = (
    WbsTask.Status.DONE,
    WbsTask.Status.ARCHIVED,
)

#: 設定が無い場合の既定値。settings 側が正だが、キー欠落で落とさない。
_FALLBACK: dict[str, Any] = {
    "MAX_ALERTS_PER_RUN": 20,
    "MAX_PROPOSALS_PER_FINDING": 3,
    "CRITICAL_PATH": {
        "DELAY_DAYS": 3,
        "MAX_DEPTH": 5,
        "MIN_IMPACTED_TASKS": 1,
        "CRITICAL_IMPACTED_TASKS": 3,
    },
    "SILENT_FIRE": {
        "STALE_UPDATE_DAYS": 10,
        "SAME_BALL_HOLDER_DAYS": 14,
        "LOW_PROGRESS_PERCENT": 30,
        "MIN_SIGNALS": 2,
        "CRITICAL_SIGNALS": 3,
    },
    "CHANGE_FREQUENCY": {
        "WINDOW_DAYS": 30,
        "BASELINE_DAYS": 120,
        "MIN_OBSERVATIONS": 6,
        "SPIKE_RATIO": 2.0,
        "CRITICAL_SPIKE_RATIO": 3.0,
    },
    "DEFECT_RATE": {
        "WINDOW_DAYS": 30,
        "BASELINE_DAYS": 120,
        "MIN_OBSERVATIONS": 10,
        "SEVERE_RATIO_PERCENT": 20,
        "OPEN_RATIO_PERCENT": 60,
        "SPIKE_RATIO": 2.0,
    },
}


def _configured() -> dict[str, Any]:
    return getattr(settings, "DETECTION_RULES", None) or {}


def rule_set(section: str) -> dict[str, Any]:
    """1 つの検知ルールのしきい値。既定値に設定値を重ねて返す。"""

    merged = dict(_FALLBACK.get(section, {}))
    merged.update(_configured().get(section) or {})

    return merged


def max_alerts_per_run() -> int:
    """1 回の実行で作るアラートの上限。"""

    return int(_configured().get("MAX_ALERTS_PER_RUN", _FALLBACK["MAX_ALERTS_PER_RUN"]))


def max_proposals_per_finding() -> int:
    """1 件の検知から作る介入提案の上限。"""

    return int(
        _configured().get(
            "MAX_PROPOSALS_PER_FINDING", _FALLBACK["MAX_PROPOSALS_PER_FINDING"]
        )
    )
