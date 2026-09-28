"""モックランタイム。LLM を使わず、全段を決定論的に進める。MVP・テスト・Web の動作確認用。

blocked_at / stopped_at を指定すると、その段で止まる経路を再現する。
"""
from __future__ import annotations

from ..core.event import ChangeEvent
from ..core.ledger import Task
from ..core.project import Project
from ..core.state import PHASES
from .base import PhaseResult, SessionHandle

_STEP_COST = 0.4  # 1 段あたりの見せかけの消費（USD）


class MockRuntime:
    name = "mock"

    def __init__(self, blocked_at: str | None = None, stopped_at: str | None = None):
        self.blocked_at, self.stopped_at = blocked_at, stopped_at
        self._n = 0

    def start(self, task: Task, event: ChangeEvent, project: Project) -> SessionHandle:
        self._n += 1
        return SessionHandle(id=f"mock_{task.task}_{self._n}", budget_usd=project.budget_usd)

    def run_phase(self, handle: SessionHandle, phase: str, task: Task, event: ChangeEvent, project: Project) -> PhaseResult:
        handle.spent_usd += _STEP_COST
        if phase == self.stopped_at or handle.spent_usd > handle.budget_usd:
            return PhaseResult("stopped", reason="budget_reached" if handle.spent_usd > handle.budget_usd else "not_converged", spent_usd=handle.spent_usd)
        if phase == self.blocked_at:
            return PhaseResult("blocked", reason="確認待ち: 期待結果を 1 つに決められない", spent_usd=handle.spent_usd)
        r = PhaseResult("ok", spent_usd=handle.spent_usd, evidence=[f"mock: {phase} gate exit 0"])
        ids = event.changed_ids or ["REQ-F-003"]
        if phase == "test-plan":
            r.impact = [f"BD-{i:03d}" for i in range(2, 2 + len(ids))] + ["DD-004", "UT-011", "ST-005"]
            r.artifacts["plan"] = "docs/quality/iso29119-test-plan.md"
            r.evidence.append("trace-check --impact REQ-F-003: exit 0")
        elif phase == "test-design":
            r.cases_added = ["ST-013", "ST-014", "UT-020"]
            r.evidence.append("spec.md#4 貸出上限")
        elif phase == "test-completion":
            r.artifacts["report"] = "docs/quality/iso29119-test-completion-report.md"
        elif phase == "regression-swap":
            r.cases_retired = ["ST-005"]
        return r

    def end(self, handle: SessionHandle) -> None:
        handle.ended = True


assert set(PHASES)  # 参照を残す（順序の定義は state.py）
