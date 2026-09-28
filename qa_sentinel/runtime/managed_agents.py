"""Managed Agents ランタイム — Codex T02 で実装する。呼び出し形は docs/01_設計仕様.md §6。

司令塔は base.Runtime の 3 メソッドしか呼ばない。ここで守ること:
- sessions.create に budget（金額上限）を必ず渡す
- agent.custom_tool_use(run_gate) はホスト側でスクリプトを実行し user.custom_tool_result を返す。LLM に exit code を解釈させない
- session.status_idle の stop_reason が budget_reached なら PhaseResult("stopped", "budget_reached")
- 人待ち（blocked / paused）では end() でセッションを終了する。待たない
"""
from __future__ import annotations

from ..core.event import ChangeEvent
from ..core.ledger import Task
from ..core.project import Project
from .base import PhaseResult, SessionHandle


class ManagedAgentsRuntime:
    name = "managed"

    def __init__(self, agent_id: str | None = None, environment_id: str | None = None):
        self.agent_id, self.environment_id = agent_id, environment_id

    def start(self, task: Task, event: ChangeEvent, project: Project) -> SessionHandle:
        raise NotImplementedError("Codex T02: client.beta.sessions.create(budget=..., resources=[repo, env.md])")

    def run_phase(self, handle: SessionHandle, phase: str, task: Task, event: ChangeEvent, project: Project) -> PhaseResult:
        raise NotImplementedError("Codex T02: user.message → events.stream → run_gate → PhaseResult")

    def end(self, handle: SessionHandle) -> None:
        raise NotImplementedError("Codex T02: sessions.archive")
