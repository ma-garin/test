"""ランタイム境界。Managed Agents でもモックでも、司令塔はこの 3 メソッドしか呼ばない。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..core.event import ChangeEvent
from ..core.ledger import Task
from ..core.project import Project


@dataclass
class SessionHandle:
    id: str
    budget_usd: float
    spent_usd: float = 0.0
    ended: bool = False


@dataclass
class PhaseResult:
    """ok: 終了条件を満たした / blocked: 人の回答待ち / stopped: 予算・収束失敗。
    LLM に exit code を解釈させない。ホストがスクリプトの結果からこれを作る。"""
    outcome: str  # ok | blocked | stopped
    reason: str | None = None
    impact: list[str] = field(default_factory=list)
    cases_added: list[str] = field(default_factory=list)
    cases_retired: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)  # 根拠（仕様の節・gate と exit code・ログのパス）
    draft: dict | None = None  # 下書きの中身（{"title","path","cases":[...]} / {"title","text"} / {"title","steps":[...]}）
    spent_usd: float = 0.0


class Runtime(Protocol):
    name: str

    def start(self, task: Task, event: ChangeEvent, project: Project) -> SessionHandle: ...

    def run_phase(self, handle: SessionHandle, phase: str, task: Task, event: ChangeEvent, project: Project) -> PhaseResult: ...

    def end(self, handle: SessionHandle) -> None: ...
