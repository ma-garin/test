"""タスク開始時の 4 問（どこまで AI が下書き／どこで止まる／だれが見る／予算）とプリセット。

docs/05_協業モデル.md §3-4 と同じ内容。順序の定義は state.PHASES。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .state import PHASES

#: プリセット名 → (AI が下書きする段, 止まる出口)。人が下書きする段 = PHASES − draft
PRESETS: dict[str, dict] = {
    "M1": {"label": "全自動", "draft": list(PHASES), "stop_at": ["regression-swap"]},
    "M2": {"label": "実装まで AI、実行は人", "draft": [p for p in PHASES if p != "test-execution"], "stop_at": ["test-design", "regression-swap"]},
    "M3": {"label": "各段承認", "draft": list(PHASES), "stop_at": list(PHASES)},
    "M4": {"label": "設計まで AI", "draft": list(PHASES[:4]), "stop_at": ["test-design"]},
    "M5": {"label": "実行と分析だけ AI", "draft": list(PHASES[5:]), "stop_at": ["regression-swap"]},
    "M6": {"label": "差し替えだけ AI", "draft": ["regression-swap"], "stop_at": ["regression-swap"]},
    "M7": {"label": "レビュー専任（人が下書き、AI がレビュー）", "draft": [], "stop_at": list(PHASES)},
    "M8": {"label": "ペア", "draft": list(PHASES), "stop_at": list(PHASES)},
}


@dataclass
class Plan:
    draft: list[str] = field(default_factory=lambda: list(PHASES))  # AI が下書きする段
    stop_at: list[str] = field(default_factory=lambda: ["regression-swap"])  # 出口で人が確定する段
    reviewer: str = ""  # 確定する人（必須）
    budget_usd: float = 5.0
    mode: str = "custom"

    def __post_init__(self) -> None:
        bad = [p for p in self.draft + self.stop_at if p not in PHASES]
        if bad:
            raise ValueError(f"unknown phase: {bad}")
        if not self.reviewer:
            raise ValueError("だれが確認するか（reviewer）が空。人の名前が要る")
        if self.budget_usd <= 0:
            raise ValueError("予算は正の値")

    def drafter(self, phase: str) -> str:
        return "ai" if phase in self.draft else "human"

    def stops(self, phase: str) -> bool:
        return phase in self.stop_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_preset(cls, name: str, reviewer: str, budget_usd: float) -> "Plan":
        if name not in PRESETS:
            raise ValueError(f"unknown preset: {name}（{', '.join(PRESETS)}）")
        p = PRESETS[name]
        return cls(draft=list(p["draft"]), stop_at=list(p["stop_at"]), reviewer=reviewer, budget_usd=budget_usd, mode=name)


def draft_until(phase: str | None) -> list[str]:
    """「この段まで AI が下書き」を段の列に直す。None は全部。"""
    if not phase:
        return list(PHASES)
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    return list(PHASES[: PHASES.index(phase) + 1])
