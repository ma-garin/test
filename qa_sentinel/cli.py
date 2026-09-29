"""qa-sentinel CLI。run（4 問）/ status / show / ok / reject / results / answer / approve / web。"""
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
from .triggers import nl, pr

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
    sub = p.add_subparsers(dest="cmd", required=False)

    r = sub.add_parser("run", help="イベントを 1 つ処理する（開始時に 4 問）")
    r.add_argument("--project", required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--nl", help="自然言語の変更指示")
    g.add_argument("--event", help="ChangeEvent の JSON ファイル")
    g.add_argument("--pr", type=int, help="PR 番号")
    r.add_argument("--mode", choices=list(PRESETS), help="プリセット（4 問を飛ばす）")
    r.add_argument("--draft-until", help="この段まで AI が下書き（段名）")
    r.add_argument("--stop-at", help="出口で止まる段（カンマ区切り、または all）")
    r.add_argument("--reviewer", help="だれが確認するか")
    r.add_argument("--budget", type=float, help="予算 USD")
    r.add_argument("--runtime", default="mock", choices=["mock", "managed"])
    r.add_argument("--mock-block-at")
    r.add_argument("--mock-stop-at")

    sub.add_parser("status", help="タスク一覧（今あなたの番のものが分かる）")

    for name, help_ in (("ok", "AI の下書きを確定して次へ"), ("reject", "差し戻す（同じ段を AI がやり直す）"),
                        ("results", "自分でやった段の結果や成果物を渡す"), ("answer", "AI の質問に答える"), ("approve", "最終承認（人だけ）")):
        a = sub.add_parser(name, help=help_)
        a.add_argument("task")
        a.add_argument("--by", required=True, help="人の名前（記録に残る）")
        a.add_argument("-m", "--note", default="", help="直した点・理由・観察")
        a.add_argument("--runtime", default="mock", choices=["mock", "managed"])
        if name == "results":
            a.add_argument("--file", default="", help="成果物のパス")
        if name == "answer":
            a.add_argument("text", help="回答")
    sh = sub.add_parser("show", help="1 タスクの詳細（履歴・決定・根拠）")
    sh.add_argument("task")
    sh.add_argument("--json", action="store_true")

    d = sub.add_parser("demo", help="図書館デモを 1 コマンドで始める（貸出上限 5→3 冊、準備まで AI）")
    d.add_argument("--by", default="demo", help="あなたの名前（既定 demo）")
    d.add_argument("--mode", default="M2", choices=list(PRESETS))
    d.add_argument("--budget", type=float, default=5.0)
    d.add_argument("--runtime", default="mock", choices=["mock", "managed"])

    w = sub.add_parser("watch", help="トラッカーを見張り、更新を検知したときだけ run_event に渡す（LLM なしのポーラー）")
    w.add_argument("--project", required=True)
    w.add_argument("--once", action="store_true", help="1 回だけポーリングして終わる")
    w.add_argument("--runtime", default="mock", choices=["mock", "managed"])
    w.add_argument("--mode", required=True, choices=list(PRESETS), help="プリセット")
    w.add_argument("--reviewer", required=True, help="だれが確認するか")
    w.add_argument("--budget", type=float, help="予算 USD")

    v = sub.add_parser("web", help="台帳の Web 表示")
    v.add_argument("--port", type=int, default=8790)
    v.add_argument("--host", default="127.0.0.1")

    args = p.parse_args(argv)
    if not args.cmd:  # 引数なし → 番号で選ぶメニュー（初心者向け）
        from .menu import main as menu
        return menu(args.tasks, args.projects)
    ledger = Ledger(args.tasks)
    try:
        if args.cmd == "run":
            project = load_project(args.project, args.projects)
            if args.pr is not None:
                ev = pr.from_github(args.project, project.config["triggers"]["pr"]["repo"], args.pr)
            elif args.nl:
                ev = nl.from_text(args.project, args.nl)
            else:
                ev = ChangeEvent.from_dict(json.load(open(args.event, encoding="utf-8")))
            plan = build_plan(args, ledger, project)
            print(f"  mode {plan.mode}  AI 下書き {len(plan.draft)}/{len(PHASES)} 段  止まる出口 {', '.join(plan.stop_at)}  確認 {plan.reviewer}  予算 ${plan.budget_usd:.2f}")
            print(_fmt(run_event(ev, _runtime(args.runtime, args), ledger, project, plan=plan)))
        elif args.cmd == "demo":
            from .web.app import DEMO_NL, DEMO_PROJECT
            project = load_project(DEMO_PROJECT, args.projects)
            plan = Plan.from_preset(args.mode, args.by, args.budget)
            t = run_event(nl.from_text(DEMO_PROJECT, DEMO_NL), _runtime(args.runtime, args), ledger, project, plan=plan)
            print(_fmt(t))
            print(f"  次: qa-sentinel show {t.task} で下書きを見て、qa-sentinel ok {t.task} --by {args.by} で次の段へ（ブラウザなら qa-sentinel web）")
        elif args.cmd == "status":
            for t in ledger.list():
                print(_fmt(t))
        elif args.cmd == "show":
            t = ledger.load(args.task)
            if args.json:
                print(json.dumps(t.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(_fmt(t))
                for ph, ev in t.evidence.items():
                    print(f"  根拠 {ph:<20} " + " / ".join(ev))
                for h in t.history:
                    print(f"  {h['at']}  {h['phase']:<20} {h['status']:<8} {h.get('reason') or ''}")
                for d in t.decisions:
                    print(f"  決定 {d['by']:<10} {d['kind']:<8} {d['phase']:<20} {d.get('note') or ''}")
        elif args.cmd == "ok":
            print(_fmt(review(args.task, args.by, True, ledger, _runtime(args.runtime, args), note=args.note, projects_dir=args.projects)))
        elif args.cmd == "reject":
            print(_fmt(review(args.task, args.by, False, ledger, _runtime(args.runtime, args), note=args.note, projects_dir=args.projects)))
        elif args.cmd == "results":
            print(_fmt(submit(args.task, args.by, ledger, _runtime(args.runtime, args), artifact=args.file, note=args.note, projects_dir=args.projects)))
        elif args.cmd == "answer":
            print(_fmt(answer(args.task, args.by, args.text, ledger, _runtime(args.runtime, args), projects_dir=args.projects)))
        elif args.cmd == "approve":
            print(_fmt(approve(args.task, args.by, ledger, projects_dir=args.projects)))
        elif args.cmd == "watch":
            import time
            from .triggers import tracker
            project = load_project(args.project, args.projects)
            state = tracker.TrackerState.load(args.tasks, args.project)
            budget = args.budget or project.budget_usd
            while True:
                try:
                    events = tracker.poll_once(project, state)
                    if not events:
                        print("polled: 0 events")
                    for ev in events:
                        plan = Plan.from_preset(args.mode, args.reviewer, budget)
                        state.sessions_started += 1
                        print(_fmt(run_event(ev, _runtime(args.runtime, args), ledger, project, plan=plan)))
                finally:
                    state.save(args.tasks, args.project)
                if args.once:
                    break
                time.sleep(float(project.config.get("triggers", {}).get("tracker", {}).get("poll_minutes", 10)) * 60)
        elif args.cmd == "web":
            from .web.app import serve
            serve(ledger, args.host, args.port, args.projects)
        return 0
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
