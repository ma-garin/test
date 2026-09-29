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
from .state import MAX_QUESTIONS_PER_TASK, MAX_REJECTS_PER_PHASE, MAX_SESSIONS_PER_TASK, next_phase, phases_from


def _end(runtime: Runtime, handle, task: Task) -> None:
    runtime.end(handle)
    task.session["ended"] = True


def run_event(event: ChangeEvent, runtime: Runtime, ledger: Ledger, project: Project | None = None,
              task: Task | None = None, plan: Plan | None = None, projects_dir: str = "projects") -> Task:
    project = project or load_project(event.project, projects_dir)
    if task is None:
        if plan is None:
            raise ValueError("新しいタスクには Plan（4 問の答え）が要る")
        task = Task(task=ledger.new_id(), project=event.project, event=event.id, plan=plan.to_dict(), mode=plan.mode,
                    branch=f"qa-sentinel/{ledger.new_id()}", nl=event.text)
        task.branch = f"qa-sentinel/{task.task}"
        ledger.save(task)
    plan = plan or Plan(**task.plan)

    phases = phases_from(event.resume_from)
    # 人が下書きする段が先頭なら、セッションを立てずに handoff
    if plan.drafter(phases[0]) == "human":
        task.record(phases[0], "handoff", reason=f"この段は {plan.reviewer} が下書き。submit で渡す")
        ledger.save(task)
        return task

    if _stop_if_over_limit(task, ledger):
        return task
    handle = runtime.start(task, event, project)
    task.session_count += 1
    task.session = {"id": handle.id, "budget_usd": handle.budget_usd, "spent_usd": 0.0, "ended": False}

    for phase in phases:
        if plan.drafter(phase) == "human":
            _end(runtime, handle, task)
            task.record(phase, "handoff", reason=f"この段は {plan.reviewer} が下書き。submit で渡す")
            ledger.save(task)
            return task
        task.record(phase, "running", session=handle.id)
        ledger.save(task)
        r = runtime.run_phase(handle, phase, task, event, project)
        task.session["spent_usd"] = round(r.spent_usd, 3)
        task.impact = sorted(set(task.impact) | set(r.impact))
        task.cases["added"] = sorted(set(task.cases["added"]) | set(r.cases_added))
        task.cases["retired"] = sorted(set(task.cases["retired"]) | set(r.cases_retired))
        task.artifacts.update(r.artifacts)
        if r.evidence:
            task.evidence[phase] = list(r.evidence)
        if r.draft:
            task.draft[phase] = r.draft
        if r.outcome == "ok":
            swap_reason = _apply_swap(task, phase, project)
            if swap_reason:
                r.outcome, r.reason = "blocked", swap_reason
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


def _continue(task: Task, from_phase: str | None, text: str, ledger: Ledger, runtime: Runtime, source: str = "approval",
              projects_dir: str = "projects") -> Task:
    if from_phase is None:
        task.record("waiting_human", "paused", reason="最終承認は人だけ。AI は approver を埋めない")
        ledger.save(task)
        return task
    ev = ChangeEvent(id=f"{task.event}_{len(task.decisions)}", project=task.project, source=source, text=text, resume_from=from_phase)
    return run_event(ev, runtime, ledger, task=task, projects_dir=projects_dir)


def _project_cwd(project: Project) -> str:
    """gate・swap を走らせる作業ディレクトリ。config [project].subdir（例: demo/library-loan）、無ければカレント。"""
    return str(project.config.get("project", {}).get("subdir") or ".")


def _apply_swap(task: Task, phase: str, project: Project) -> str | None:
    """regression-swap 段の下書き（新ケース行）を実際の CSV に反映する（失効は削除しない）。
    config [project].apply_swap が false なら何もしない。失敗（gate NG・例外）は理由を返し、呼び元が blocked にする。"""
    if phase != "regression-swap" or not project.config.get("project", {}).get("apply_swap", False):
        return None
    cases = [c for c in (task.draft.get("test-design") or {}).get("cases", []) if c.get("state") == "new"]
    if not cases:
        return None
    from .swap import swap
    new_rows = [{"対象機能": c.get("target", ""), "テスト目的": c.get("purpose", ""), "手順": c.get("steps", ""), "期待される結果": c.get("expected", ""),
                 "severity": c.get("sev", ""), "根拠の版": c.get("basis", ""), "req_id": (c.get("basis") or "").split("@")[0] or None,
                 "prefix": (c.get("id") or "ST").split("-")[0]} for c in cases]
    try:
        r = swap(project, [i for i in task.impact if i.startswith("REQ")] or task.impact, new_rows, cwd=_project_cwd(project))
    except Exception as e:  # noqa: BLE001 - CSV や追跡表が無い等。台帳に残して人へ戻す
        task.evidence.setdefault(phase, []).append(f"swap: 失敗 {type(e).__name__}: {e}")
        return f"swap_error: {type(e).__name__}"
    task.evidence.setdefault(phase, []).append(f"swap: {'ok' if r.ok else 'NG'} 失効 {len(r.retired)} 追加 {len(r.added)}" + (f" ({r.reason})" if r.reason else ""))
    if not r.ok:
        return f"swap_ng: {r.reason or 'gate NG'}（CSV は元に戻した）"
    task.cases["added"] = sorted(set(task.cases["added"]) | set(r.added))
    task.cases["retired"] = sorted(set(task.cases["retired"]) | set(r.retired))
    return None


def check_limits(task: Task) -> str | None:
    """L5〜L7 の上限判定（docs/06 §3）。判定順は sessions > questions > rejects。超過なら理由、なければ None。"""
    if task.session_count >= MAX_SESSIONS_PER_TASK:
        return "sessions_exceeded"
    if sum(1 for d in task.decisions if d["kind"] == "answer") >= MAX_QUESTIONS_PER_TASK:
        return "questions_exceeded"
    if sum(1 for d in task.decisions if d["kind"] == "reject" and d["phase"] == task.phase) >= MAX_REJECTS_PER_PHASE:
        return f"rejects_exceeded:{task.phase}"
    return None


def _stop_if_over_limit(task: Task, ledger: Ledger) -> bool:
    reason = check_limits(task)
    if reason:
        task.record(task.phase, "stopped", reason=f"{reason} — 進め方を見直してください（docs/06 §3）")
        ledger.save(task)
        return True
    return False


def review(task_id: str, by: str, ok: bool, ledger: Ledger, runtime: Runtime, note: str = "", projects_dir: str = "projects") -> Task:
    """段の出口での確定（ok）または差し戻し（同じ段を AI がやり直す）。"""
    task = ledger.load(task_id)
    if task.status != "review":
        raise ValueError(f"{task_id} は確定待ちではない（status={task.status}）")
    if not by:
        raise ValueError("確定者の名前が要る")
    task.decisions.append({"at": task.updated_at, "by": by, "kind": "confirm" if ok else "reject", "phase": task.phase, "note": note})
    if _stop_if_over_limit(task, ledger):
        return task
    if ok:
        return _continue(task, next_phase(task.phase), note, ledger, runtime, projects_dir=projects_dir)
    return _continue(task, task.phase, f"差し戻し: {note}", ledger, runtime, projects_dir=projects_dir)


def submit(task_id: str, by: str, ledger: Ledger, runtime: Runtime, artifact: str = "", note: str = "", projects_dir: str = "projects") -> Task:
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
    return _continue(task, next_phase(task.phase), note, ledger, runtime, projects_dir=projects_dir)


def answer(task_id: str, by: str, text: str, ledger: Ledger, runtime: Runtime, projects_dir: str = "projects") -> Task:
    """確認待ち（blocked）への回答。止まった段から continue。"""
    task = ledger.load(task_id)
    if task.status != "blocked":
        raise ValueError(f"{task_id} は確認待ちではない（status={task.status}）")
    if not by:
        raise ValueError("回答者の名前が要る")
    task.decisions.append({"at": task.updated_at, "by": by, "kind": "answer", "phase": task.phase, "note": text})
    if _stop_if_over_limit(task, ledger):
        return task
    return _continue(task, task.phase, text, ledger, runtime, projects_dir=projects_dir)


def approve(task_id: str, by: str, ledger: Ledger, gate: bool = True, projects_dir: str = "projects") -> Task:
    """最終承認（人だけ）。gate=True なら check-approval を機械判定し、NG なら台帳を変えずに ValueError。スクリプト無し（127）は通す。"""
    task = ledger.load(task_id)
    if task.status != "paused":
        raise ValueError(f"{task_id} は承認待ちではない（status={task.status}）")
    if not by:
        raise ValueError("承認者の名前が要る")
    entry = {"at": task.updated_at, "by": by, "kind": "approve", "phase": "done", "note": ""}
    project = load_project(task.project, projects_dir) if gate else None
    gate_name = project.config.get("project", {}).get("approval_gate", "") if project else ""
    if gate and not gate_name:
        entry["gate"] = "skipped(config)"  # [project].approval_gate が空: 工程承認の機械判定は使わない
    elif gate:
        from . import gates
        result = gates.run_gate(gate_name, [], project, cwd=_project_cwd(project))
        if result.exit_code == 127:
            entry["gate"] = "missing"
        elif not result.ok:
            raise ValueError(f"check-approval NG: {result.summary}")
        else:
            entry["gate"] = "ok"
    task.decisions.append(entry)
    task.record("done", "done", reason=None, approver=by)
    ledger.save(task)
    return task
