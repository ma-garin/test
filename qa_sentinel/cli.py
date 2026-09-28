"""qa-sentinel CLI。run（4 問）/ status / review / submit / results / answer / approve / web。"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .core.event import ChangeEvent
from .core.ledger import Ledger
from .core.orchestrator import answer, approve, review, run_event, submit
from .core.plan import PRESETS, Plan, draft_until
from .core.project import load_project
from .core.state import PHASE_LABELS, PHASES
from .runtime.mock import MockRuntime
from .triggers import nl

_LAST = ".last-plan.json"  # 4 問の既定値（前回の選択）を台帳ディレクトリに置く


def _runtime(name: str, args):
    if name == "mock":
        return MockRuntime(blocked_at=getattr(args, "mock_block_at", None), stopped_at=getattr(args, "mock_stop_at", None))
    if name == "managed":
        from .runtime.managed_agents import ManagedAgentsRuntime
        return ManagedAgentsRuntime()
    raise SystemExit(f"unknown runtime: {name}")


def _fmt(t) -> str:
    s = t.session or {}
    who = {"review": "確定待ち", "handoff": "手渡し", "blocked": "回答待ち", "paused": "承認待ち"}.get(t.status, t.status)
    return (f"{t.task}  {t.project:<14} {t.mode:<7} {PHASE_LABELS.get(t.phase, t.phase):<16} {who:<8} "
            f"${s.get('spent_usd', 0):.2f}/{s.get('budget_usd', 0):.2f}  {t.reason or ''}")


def _ask(prompt: str, default: str) -> str:
    """4 問。TTY でなければ既定値をそのまま使う。"""
    if not sys.stdin.isatty():
        return default
    v = input(f"? {prompt} › [{default}] ").strip()
    return v or default


def build_plan(args, ledger: Ledger, project) -> Plan:
    last = {}
    try:
        last = json.loads((ledger.root / _LAST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cfg = project.config.get("session", {})
    if args.mode:
        plan = Plan.from_preset(args.mode, args.reviewer or last.get("reviewer") or _ask("だれが確認しますか", last.get("reviewer", os.environ.get("USER", ""))),
                                args.budget or last.get("budget_usd") or float(cfg.get("budget_usd", 5.0)))
    else:
        until = args.draft_until or _ask("どの段まで AI が下書きしますか（段名。全部なら Enter）", last.get("draft_until", "") or "")
        stop = args.stop_at or _ask("どの段の出口で止まって確認しますか（カンマ区切り。全部なら all）", ",".join(last.get("stop_at", ["regression-swap"])))
        reviewer = args.reviewer or _ask("だれが確認しますか", last.get("reviewer", os.environ.get("USER", "")))
        budget = args.budget or float(_ask("このタスクの予算（USD）", str(last.get("budget_usd", cfg.get("budget_usd", 5.0)))))
        stop_at = list(PHASES) if stop.strip() == "all" else [s.strip() for s in stop.split(",") if s.strip()]
        plan = Plan(draft=draft_until(until or None), stop_at=stop_at, reviewer=reviewer, budget_usd=float(budget), mode="custom")
    try:
        (ledger.root / _LAST).write_text(json.dumps({"draft_until": plan.draft[-1] if plan.draft else "", "stop_at": plan.stop_at,
                                                     "reviewer": plan.reviewer, "budget_usd": plan.budget_usd}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return plan


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="qa-sentinel")
    p.add_argument("--tasks", default="tasks", help="台帳ディレクトリ")
    p.add_argument("--projects", default="projects")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="イベントを 1 つ処理する（開始時に 4 問）")
    r.add_argument("--project", required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--nl", help="自然言語の変更指示")
    g.add_argument("--event", help="ChangeEvent の JSON ファイル")
    r.add_argument("--mode", choices=list(PRESETS), help="プリセット（4 問を飛ばす）")
    r.add_argument("--draft-until", help="この段まで AI が下書き（段名）")
    r.add_argument("--stop-at", help="出口で止まる段（カンマ区切り、または all）")
    r.add_argument("--reviewer", help="だれが確認するか")
    r.add_argument("--budget", type=float, help="予算 USD")
    r.add_argument("--runtime", default="mock", choices=["mock", "managed"])
    r.add_argument("--mock-block-at")
    r.add_argument("--mock-stop-at")

    s = sub.add_parser("status", help="台帳を表示")
    s.add_argument("task", nargs="?")
    s.add_argument("--json", action="store_true")

    for name, help_ in (("review", "段の出口で確定（--ok）または差し戻し（--reject）"), ("submit", "人が下書きした段を渡す"),
                        ("answer", "確認待ちに回答"), ("approve", "最終承認（人だけ）")):
        a = sub.add_parser(name, help=help_)
        a.add_argument("task")
        a.add_argument("--by", required=True, help="人の名前")
        a.add_argument("--runtime", default="mock", choices=["mock", "managed"])
        if name == "review":
            m = a.add_mutually_exclusive_group(required=True)
            m.add_argument("--ok", action="store_true")
            m.add_argument("--reject", metavar="理由")
            a.add_argument("--note", default="")
        if name == "submit":
            a.add_argument("--file", default="", help="成果物のパス")
            a.add_argument("--note", default="")
        if name == "answer":
            a.add_argument("--text", required=True)

    v = sub.add_parser("web", help="台帳の Web 表示")
    v.add_argument("--port", type=int, default=8790)
    v.add_argument("--host", default="127.0.0.1")

    args = p.parse_args(argv)
    ledger = Ledger(args.tasks)
    try:
        if args.cmd == "run":
            project = load_project(args.project, args.projects)
            ev = nl.from_text(args.project, args.nl) if args.nl else ChangeEvent.from_dict(json.load(open(args.event, encoding="utf-8")))
            plan = build_plan(args, ledger, project)
            print(f"  mode {plan.mode}  AI 下書き {len(plan.draft)}/{len(PHASES)} 段  止まる出口 {', '.join(plan.stop_at)}  確認 {plan.reviewer}  予算 ${plan.budget_usd:.2f}")
            print(_fmt(run_event(ev, _runtime(args.runtime, args), ledger, project, plan=plan)))
        elif args.cmd == "status":
            if args.task:
                t = ledger.load(args.task)
                if args.json:
                    print(json.dumps(t.to_dict(), ensure_ascii=False, indent=2))
                else:
                    print(_fmt(t))
                    for h in t.history:
                        print(f"  {h['at']}  {h['phase']:<20} {h['status']:<8} {h.get('reason') or ''}")
                    for d in t.decisions:
                        print(f"  決定 {d['by']:<10} {d['kind']:<8} {d['phase']:<20} {d.get('note') or ''}")
            else:
                for t in ledger.list():
                    print(_fmt(t))
        elif args.cmd == "review":
            print(_fmt(review(args.task, args.by, args.ok, ledger, _runtime(args.runtime, args), note=args.note or (args.reject or ""))))
        elif args.cmd == "submit":
            print(_fmt(submit(args.task, args.by, ledger, _runtime(args.runtime, args), artifact=args.file, note=args.note)))
        elif args.cmd == "answer":
            print(_fmt(answer(args.task, args.by, args.text, ledger, _runtime(args.runtime, args))))
        elif args.cmd == "approve":
            print(_fmt(approve(args.task, args.by, ledger)))
        elif args.cmd == "web":
            from .web.app import serve
            serve(ledger, args.host, args.port)
        return 0
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
