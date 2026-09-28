"""qa-sentinel CLI。run / status / approve / answer / web。"""
from __future__ import annotations

import argparse
import json
import sys

from .core.event import ChangeEvent
from .core.ledger import Ledger
from .core.orchestrator import answer, approve, run_event
from .core.project import load_project
from .core.state import PHASE_LABELS
from .runtime.mock import MockRuntime
from .triggers import nl


def _runtime(name: str, args):
    if name == "mock":
        return MockRuntime(blocked_at=getattr(args, "mock_block_at", None), stopped_at=getattr(args, "mock_stop_at", None))
    if name == "managed":
        from .runtime.managed_agents import ManagedAgentsRuntime
        return ManagedAgentsRuntime()
    raise SystemExit(f"unknown runtime: {name}")


def _fmt(t) -> str:
    s = t.session or {}
    return f"{t.task}  {t.project:<14} {PHASE_LABELS.get(t.phase, t.phase):<16} {t.status:<8} ${s.get('spent_usd', 0):.2f}/{s.get('budget_usd', 0):.2f}  {t.reason or ''}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="qa-sentinel")
    p.add_argument("--tasks", default="tasks", help="台帳ディレクトリ")
    p.add_argument("--projects", default="projects")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="イベントを 1 つ処理する")
    r.add_argument("--project", required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--nl", help="自然言語の変更指示")
    g.add_argument("--event", help="ChangeEvent の JSON ファイル（relay ポーラー・PR 受信から）")
    r.add_argument("--runtime", default="mock", choices=["mock", "managed"])
    r.add_argument("--mock-block-at", help="モック: この段で確認待ちにする")
    r.add_argument("--mock-stop-at", help="モック: この段で停止する")

    s = sub.add_parser("status", help="台帳を表示")
    s.add_argument("task", nargs="?")
    s.add_argument("--json", action="store_true")

    a = sub.add_parser("approve", help="承認待ちタスクを完了にする（人だけ）")
    a.add_argument("task")
    a.add_argument("--by", required=True)

    w = sub.add_parser("answer", help="確認待ちに回答し、止まった段から再開する")
    w.add_argument("task")
    w.add_argument("--text", required=True)
    w.add_argument("--runtime", default="mock", choices=["mock", "managed"])

    v = sub.add_parser("web", help="台帳の Web 表示")
    v.add_argument("--port", type=int, default=8790)
    v.add_argument("--host", default="127.0.0.1")

    args = p.parse_args(argv)
    ledger = Ledger(args.tasks)

    if args.cmd == "run":
        project = load_project(args.project, args.projects)
        if args.nl:
            ev = nl.from_text(args.project, args.nl)
        else:
            with open(args.event, encoding="utf-8") as f:
                ev = ChangeEvent.from_dict(json.load(f))
        t = run_event(ev, _runtime(args.runtime, args), ledger, project)
        print(_fmt(t))
        return 0
    if args.cmd == "status":
        if args.task:
            t = ledger.load(args.task)
            if args.json:
                print(json.dumps(t.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(_fmt(t))
                for h in t.history:
                    print(f"  {h['at']}  {h['phase']:<20} {h['status']:<8} {h.get('reason') or ''}")
            return 0
        for t in ledger.list():
            print(_fmt(t))
        return 0
    if args.cmd == "approve":
        print(_fmt(approve(args.task, args.by, ledger)))
        return 0
    if args.cmd == "answer":
        print(_fmt(answer(args.task, args.text, ledger, _runtime(args.runtime, args))))
        return 0
    if args.cmd == "web":
        from .web.app import serve
        serve(ledger, args.host, args.port)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
