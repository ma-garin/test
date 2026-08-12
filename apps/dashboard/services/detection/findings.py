"""検知結果と、検知しなかった理由を表す値オブジェクト。

「何を検知したか」と同じ重さで「何を、なぜ検知しなかったか」を残す。
判定不能（母数不足）を黙って捨てると、データが無いのか安全なのか区別できない。
すべて frozen dataclass にして、検知器が返した内容を後段で書き換えられないようにする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.projects.models import Project


@dataclass(frozen=True)
class Finding:
    """しきい値を超えたと判定した 1 件。"""

    project: Project
    kind: str
    #: 同じ対象を重複して検知しないための鍵。アラートの evidence に保存する。
    dedupe_key: str
    category: str
    severity: str
    title: str
    detail: str
    #: 判定根拠。observed / threshold / reason を必ず含める。
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return str(self.evidence.get("reason", ""))


class SkipReason:
    """検知しなかった理由コード。画面と CLI で同じ語彙を使う。"""

    INSUFFICIENT_DATA = "insufficient_data"
    WITHIN_THRESHOLD = "within_threshold"
    DUPLICATE = "duplicate"
    LIMIT_REACHED = "limit_reached"


SKIP_LABELS = {
    SkipReason.INSUFFICIENT_DATA: "判定不能（観測数不足）",
    SkipReason.WITHIN_THRESHOLD: "しきい値内",
    SkipReason.DUPLICATE: "既に未対応のアラートあり",
    SkipReason.LIMIT_REACHED: "1回あたりの上限に到達",
}


@dataclass(frozen=True)
class Skip:
    """検知しなかった 1 件。理由を必ず持たせる。"""

    project: Project
    kind: str
    reason: str
    detail: str = ""

    @property
    def reason_label(self) -> str:
        return SKIP_LABELS.get(self.reason, self.reason)

    @property
    def is_undetermined(self) -> bool:
        """母数不足で「判定不能」だったか。安全（しきい値内）とは区別する。"""

        return self.reason == SkipReason.INSUFFICIENT_DATA


#: 検知種別の表示名。画面・CLI・アラート本文で同じ名前を使う。
KIND_LABELS = {
    "critical_path": "クリティカルパス影響",
    "silent_fire": "サイレント炎上の予兆",
    "change_frequency": "仕様変更頻度の異常",
    "defect_rate": "バグ率の異常",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)
