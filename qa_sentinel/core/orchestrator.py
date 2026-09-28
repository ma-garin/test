"""司令塔。イベント 1 つにつきセッション 1 つを起動し、PHASES を進め、台帳に書く。

- 人待ち（blocked / paused）ではセッションを終了する。自分を起こす仕組みは持たない
- 再開は resume_from を持つ新しいイベント（approval）で、上流をやり直さず続ける
"""
from __future__ import annotations

from ..runtime.base import Runtime
from .event import ChangeEvent
from .ledger import Ledger, Task
from .project import Project, load_project
from .state import phases_from


def run_event(event: ChangeEvent, runtime: Runtime, ledger: Ledger, project: Project | None = None, task: Task | None = None) -> Task:
    project = project or load_project(event.project)
    if task is None:
        task = Task(task=ledger.new_id(), project=event.project, event=event.id)
        ledger.save(task)
    handle = runtime.start(task, event, project)
    task.session = {"id": handle.id, "budget_usd": handle.budget_usd, "spent_usd": 0.0, "ended": False}

    for phase in phases_from(event.resume_from):
        task.record(phase, "running")
        ledger.save(task)
        r = runtime.run_phase(handle, phase, task, event, project)
        task.session["spent_usd"] = round(r.spent_usd, 3)
        task.impact = sorted(set(task.impact) | set(r.impact))
        task.cases["added"] = sorted(set(task.cases["added"]) | set(r.cases_added))
        task.cases["retired"] = sorted(set(task.cases["retired"]) | set(r.cases_retired))
        task.artifacts.update(r.artifacts)
        if r.outcome == "ok":
            task.record(phase, "done")
            ledger.save(task)
            continue
        # blocked / stopped: セッションを終了して人へ
        runtime.end(handle)
        task.session["ended"] = True
        task.record(phase, "blocked" if r.outcome == "blocked" else "stopped", reason=r.reason)
        ledger.save(task)
        return task

    runtime.end(handle)
    task.session["ended"] = True
    task.record("waiting_human", "paused", reason="approver は人だけ。AI は埋めない")
    ledger.save(task)
    return task


def approve(task_id: str, by: str, ledger: Ledger) -> Task:
    """人の承認。check-approval.sh 相当の判定はここでは台帳の状態だけで行う（本実装は T05）。"""
    task = ledger.load(task_id)
    if task.status != "paused":
        raise ValueError(f"{task_id} は承認待ちではない（status={task.status}）")
    task.record("done", "done", reason=None, approver=by)
    ledger.save(task)
    return task


def answer(task_id: str, text: str, ledger: Ledger, runtime: Runtime) -> Task:
    """blocked（確認待ち）への回答。回答を approval イベントにして、止まった段から continue する。"""
    task = ledger.load(task_id)
    if task.status != "blocked":
        raise ValueError(f"{task_id} は確認待ちではない（status={task.status}）")
    ev = ChangeEvent(id=f"{task.event}_answer", project=task.project, source="approval", text=text, resume_from=task.phase)
    return run_event(ev, runtime, ledger, task=task)
