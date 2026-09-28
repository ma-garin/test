"""司令塔。イベント 1 つにつきセッション 1 つを起動し、Plan に従って PHASES を進め、台帳に書く。

協業モデル（docs/05）:
- 段ごとに drafter（ai|human）。human の段は handoff で止まる（人が submit する）
- stop_at の段は、AI の下書きが終わったら review で止まる（人が review --ok/--reject する）
- 止まるときは必ずセッションを終了する。再開は resume_from 付きの新イベント（新セッション）
- 確定・回答・承認は人の名前で decisions[] に残す
"""
from __future__ import annotations

from ..runtime.base import Runtime
from .event import ChangeEvent
from .ledger import Ledger, Task
from .plan import Plan
from .project import Project, load_project
from .state import next_phase, phases_from


def _end(runtime: Runtime, handle, task: Task) -> None:
    runtime.end(handle)
    task.session["ended"] = True


def run_event(event: ChangeEvent, runtime: Runtime, ledger: Ledger, project: Project | None = None,
              task: Task | None = None, plan: Plan | None = None) -> Task:
    project = project or load_project(event.project)
    if task is None:
        if plan is None:
            raise ValueError("新しいタスクには Plan（4 問の答え）が要る")
        task = Task(task=ledger.new_id(), project=event.project, event=event.id, plan=plan.to_dict(), mode=plan.mode,
                    branch=f"qa-sentinel/{ledger.new_id()}")
        task.branch = f"qa-sentinel/{task.task}"
        ledger.save(task)
    plan = plan or Plan(**task.plan)

    phases = phases_from(event.resume_from)
    # 人が下書きする段が先頭なら、セッションを立てずに handoff
    if plan.drafter(phases[0]) == "human":
        task.record(phases[0], "handoff", reason=f"この段は {plan.reviewer} が下書き。submit で渡す")
        ledger.save(task)
        return task

    handle = runtime.start(task, event, project)
    task.session = {"id": handle.id, "budget_usd": handle.budget_usd, "spent_usd": 0.0, "ended": False}

    for phase in phases:
        if plan.drafter(phase) == "human":
            _end(runtime, handle, task)
            task.record(phase, "handoff", reason=f"この段は {plan.reviewer} が下書き。submit で渡す")
            ledger.save(task)
            return task
        task.record(phase, "running")
        ledger.save(task)
        r = runtime.run_phase(handle, phase, task, event, project)
        task.session["spent_usd"] = round(r.spent_usd, 3)
        task.impact = sorted(set(task.impact) | set(r.impact))
        task.cases["added"] = sorted(set(task.cases["added"]) | set(r.cases_added))
        task.cases["retired"] = sorted(set(task.cases["retired"]) | set(r.cases_retired))
        task.artifacts.update(r.artifacts)
        if r.evidence:
            task.evidence[phase] = list(r.evidence)
        if r.outcome != "ok":
            _end(runtime, handle, task)
            task.record(phase, "blocked" if r.outcome == "blocked" else "stopped", reason=r.reason)
            ledger.save(task)
            return task
        task.record(phase, "done")
        if plan.stops(phase):
            _end(runtime, handle, task)
            task.record(phase, "review", reason=f"{plan.reviewer} の確定待ち（根拠 {len(task.evidence.get(phase, []))} 件）")
            ledger.save(task)
            return task
        ledger.save(task)

    _end(runtime, handle, task)
    task.record("waiting_human", "paused", reason="最終承認は人だけ。AI は approver を埋めない")
    ledger.save(task)
    return task


def _continue(task: Task, from_phase: str | None, text: str, ledger: Ledger, runtime: Runtime, source: str = "approval") -> Task:
    if from_phase is None:
        task.record("waiting_human", "paused", reason="最終承認は人だけ。AI は approver を埋めない")
        ledger.save(task)
        return task
    ev = ChangeEvent(id=f"{task.event}_{len(task.decisions)}", project=task.project, source=source, text=text, resume_from=from_phase)
    return run_event(ev, runtime, ledger, task=task)


def review(task_id: str, by: str, ok: bool, ledger: Ledger, runtime: Runtime, note: str = "") -> Task:
    """段の出口での確定（ok）または差し戻し（同じ段を AI がやり直す）。"""
    task = ledger.load(task_id)
    if task.status != "review":
        raise ValueError(f"{task_id} は確定待ちではない（status={task.status}）")
    if not by:
        raise ValueError("確定者の名前が要る")
    task.decisions.append({"at": task.updated_at, "by": by, "kind": "confirm" if ok else "reject", "phase": task.phase, "note": note})
    if ok:
        return _continue(task, next_phase(task.phase), note, ledger, runtime)
    return _continue(task, task.phase, f"差し戻し: {note}", ledger, runtime)


def submit(task_id: str, by: str, ledger: Ledger, runtime: Runtime, artifact: str = "", note: str = "") -> Task:
    """人が下書きした段の成果物を渡す（handoff → 次の段）。"""
    task = ledger.load(task_id)
    if task.status != "handoff":
        raise ValueError(f"{task_id} は手渡し待ちではない（status={task.status}）")
    if not by:
        raise ValueError("提出者の名前が要る")
    task.decisions.append({"at": task.updated_at, "by": by, "kind": "submit", "phase": task.phase, "note": note, "artifact": artifact})
    if artifact:
        task.artifacts[task.phase] = artifact
    task.record(task.phase, "done", reason=f"{by} が下書き")
    return _continue(task, next_phase(task.phase), note, ledger, runtime)


def answer(task_id: str, by: str, text: str, ledger: Ledger, runtime: Runtime) -> Task:
    """確認待ち（blocked）への回答。止まった段から continue。"""
    task = ledger.load(task_id)
    if task.status != "blocked":
        raise ValueError(f"{task_id} は確認待ちではない（status={task.status}）")
    if not by:
        raise ValueError("回答者の名前が要る")
    task.decisions.append({"at": task.updated_at, "by": by, "kind": "answer", "phase": task.phase, "note": text})
    return _continue(task, task.phase, text, ledger, runtime)


def approve(task_id: str, by: str, ledger: Ledger) -> Task:
    """最終承認。check-approval.sh 相当の判定は T05 で接続する。"""
    task = ledger.load(task_id)
    if task.status != "paused":
        raise ValueError(f"{task_id} は承認待ちではない（status={task.status}）")
    if not by:
        raise ValueError("承認者の名前が要る")
    task.decisions.append({"at": task.updated_at, "by": by, "kind": "approve", "phase": "done", "note": ""})
    task.record("done", "done", reason=None, approver=by)
    ledger.save(task)
    return task
