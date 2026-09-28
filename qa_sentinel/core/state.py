"""工程（PHASES）と状態の定義。順序の唯一の定義。CLI・Web・ランタイムはここを参照する。

順序は webspec2doc `process_gate.PHASES` を採用し、regression-swap と人の段を足した。
"""
from __future__ import annotations

PHASES: tuple[str, ...] = (
    "test-plan",
    "test-analysis",
    "test-strategy",
    "test-design",
    "test-implementation",
    "test-execution",
    "defect-analysis",
    "test-completion",
    "regression-swap",
)

PHASE_LABELS: dict[str, str] = {
    "test-plan": "テスト計画（影響分析）",
    "test-analysis": "テスト基本設計",
    "test-strategy": "テスト詳細設計",
    "test-design": "ケース設計",
    "test-implementation": "ケース実装",
    "test-execution": "実行・修整",
    "defect-analysis": "不具合分析",
    "test-completion": "完了報告",
    "regression-swap": "リグレッション差し替え",
    "waiting_human": "人の承認待ち",
    "done": "完了",
}

#: 段の終了条件（機械）と止まる条件（人へ）。文書 01 §2 と同じ内容。
GATES: dict[str, dict[str, str]] = {
    "test-plan": {"gate": "影響 ID 一覧と範囲が埋まる", "stop": "追跡表に無い変更（ID 未割当）"},
    "test-analysis": {"gate": "条件一覧あり", "stop": ""},
    "test-strategy": {"gate": "方針表に対象外が明示", "stop": ""},
    "test-design": {"gate": "全ケースに期待結果", "stop": "期待結果を決められない"},
    "test-implementation": {"gate": "lint NG=0", "stop": ""},
    "test-execution": {"gate": "Critical/High 残ゼロ・test-metrics --gate 0", "stop": "2 周で収束せず／原因が設計"},
    "defect-analysis": {"gate": "全 FAIL に分類", "stop": ""},
    "test-completion": {"gate": "metrics が埋まる", "stop": ""},
    "regression-swap": {"gate": "trace-check NG=0 かつ test-weaken-check NG=0", "stop": "どちらか NG → 戻す"},
}

STATUSES: tuple[str, ...] = ("queued", "running", "review", "handoff", "blocked", "paused", "stopped", "done")
#: review=AI の下書き完了・人の確定待ち / handoff=人が下書きする段・入力待ち / blocked=確認待ち / paused=最終承認待ち


def next_phase(phase: str) -> str | None:
    """次の段。最後なら None（→ waiting_human）。"""
    if phase not in PHASES:
        return None
    i = PHASES.index(phase)
    return PHASES[i + 1] if i + 1 < len(PHASES) else None


def phases_from(phase: str | None) -> tuple[str, ...]:
    """resume_from を含めた以降の段。None なら全段。"""
    if not phase:
        return PHASES
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    return PHASES[PHASES.index(phase):]
