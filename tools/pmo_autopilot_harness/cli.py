"""PMO 自律運用化の機械可読キューを安全に操作する CLI。"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from tools.pmo_autopilot_harness.contracts import AGENT_DIR, ContractError, load_repository_contract

ATTEMPTS_PATH = Path("var/pmo_autopilot_harness/attempts.json")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
IMMEDIATE_HOLD = {"credential", "permission", "policy", "secrets"}


def _queue_path() -> Path:
    return AGENT_DIR / "pmo_autopilot_queue.json"


def _load() -> dict[str, dict]:
    return load_repository_contract()


def _save_queue(package: dict[str, dict]) -> None:
    _queue_path().write_text(
        json.dumps(package["queue"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _ticket(package: dict[str, dict], ticket_id: str) -> dict:
    for ticket in package["queue"]["tickets"]:
        if ticket["id"] == ticket_id:
            return ticket
    raise ContractError(f"チケットがありません: {ticket_id}")


def _ready(package: dict[str, dict]) -> list[dict]:
    tickets = package["queue"]["tickets"]
    status = {ticket["id"]: ticket["state"] for ticket in tickets}
    ready = [
        ticket
        for ticket in tickets
        if ticket["state"] in {"untouched", "in_progress"}
        and all(status[dependency] == "done" for dependency in ticket["depends_on"])
    ]
    return sorted(ready, key=lambda item: (PRIORITY_ORDER[item["priority"]], item["id"]))


def _print_ticket(ticket: dict) -> None:
    print(f"{ticket['id']} [{ticket['priority']}/{ticket['kind']}] {ticket['state']}")
    print(f"  目標: {ticket['goal']}")
    print("  受入条件:")
    for condition in ticket["acceptance"]:
        print(f"    - {condition}")
    print("  検証:")
    for command in ticket["verification"] or ["（実装前のため未定義）"]:
        print(f"    - {command}")


def cmd_validate(_: argparse.Namespace) -> int:
    package = _load()
    print(
        "OK: contract / decisions / scenarios / queue が整合。"
        f" tickets={len(package['queue']['tickets'])}, scenarios={len(package['scenarios']['scenarios'])}"
    )
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    package = _load()
    counts: dict[str, int] = {}
    for ticket in package["queue"]["tickets"]:
        counts[ticket["state"]] = counts.get(ticket["state"], 0) + 1
    print("状態: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    ready = _ready(package)
    print("着手可能: " + (", ".join(ticket["id"] for ticket in ready) or "なし"))
    for ticket in package["queue"]["tickets"]:
        if ticket["state"] in {"hold", "decision_required"}:
            print(f"停止: {ticket['id']} — {ticket.get('hold_reason', '')}")
    return 0


def cmd_next(_: argparse.Namespace) -> int:
    package = _load()
    ready = _ready(package)
    if not ready:
        print("着手可能なチケットはありません。decision_required / hold / 依存を確認してください。")
        return 0
    in_progress = [ticket for ticket in ready if ticket["state"] == "in_progress"]
    _print_ticket((in_progress or ready)[0])
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    package = _load()
    ticket = _ticket(package, args.id)
    if ticket not in _ready(package):
        raise ContractError(f"{args.id}: 依存未解決または停止中のため開始できません。")
    ticket["state"] = "in_progress"
    ticket["executor"] = args.executor
    _save_queue(package)
    _print_ticket(ticket)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    package = _load()
    ticket = _ticket(package, args.id)
    failed = set(args.failed or [])
    unknown = failed - set(ticket["required_reviews"])
    if unknown:
        raise ContractError(f"{args.id}: 未知の review 項目 {sorted(unknown)}")
    ticket["review"] = {
        "reviewer": args.reviewer,
        "passed": [item for item in ticket["required_reviews"] if item not in failed],
        "failed": sorted(failed),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    _save_queue(package)
    print(f"{args.id}: review を記録しました。")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    package = _load()
    ticket = _ticket(package, args.id)
    if ticket["state"] != "in_progress":
        raise ContractError(f"{args.id}: in_progress 以外は done にできません。")
    review = ticket.get("review") or {}
    if review.get("failed") or set(review.get("passed", [])) != set(ticket["required_reviews"]):
        raise ContractError(f"{args.id}: required_reviews の承認が不足しています。")
    if review.get("reviewer") == ticket.get("executor"):
        raise ContractError(f"{args.id}: 実装者自身のレビューだけでは done にできません。")
    missing = set(ticket["required_scenarios"]) - set(args.evidence)
    if missing:
        raise ContractError(f"{args.id}: required_scenarios の証跡が不足しています: {sorted(missing)}")
    ticket["evidence"] = list(dict.fromkeys([*ticket["evidence"], *args.evidence]))
    ticket["state"] = "done"
    _save_queue(package)
    print(f"{args.id}: done")
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    package = _load()
    ticket = _ticket(package, args.id)
    ticket["state"] = "hold"
    ticket["hold_reason"] = args.reason
    _save_queue(package)
    print(f"{args.id}: hold — {args.reason}")
    return 0


def _load_attempts() -> list[dict]:
    if not ATTEMPTS_PATH.exists():
        return []
    return json.loads(ATTEMPTS_PATH.read_text(encoding="utf-8"))


def cmd_fail(args: argparse.Namespace) -> int:
    package = _load()
    ticket = _ticket(package, args.id)
    attempts = _load_attempts()
    entry = {
        "ticket_id": args.id,
        "category": args.category,
        "summary": args.summary,
        "at": datetime.now(UTC).isoformat(),
    }
    attempts.append(entry)
    ATTEMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ATTEMPTS_PATH.write_text(json.dumps(attempts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    same_count = sum(
        1
        for item in attempts
        if item["ticket_id"] == args.id and item["category"] == args.category
    )
    if args.category in IMMEDIATE_HOLD or same_count >= 3:
        ticket["state"] = "hold"
        ticket["hold_reason"] = f"{args.category} が {same_count} 回: {args.summary}"
        _save_queue(package)
        print(f"{args.id}: hold")
    else:
        print(f"{args.id}: {args.category} {same_count} 回目。修正して再検証してください。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pmo_autopilot_harness")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate").set_defaults(func=cmd_validate)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("next").set_defaults(func=cmd_next)
    start = sub.add_parser("start")
    start.add_argument("id")
    start.add_argument("--executor", required=True)
    start.set_defaults(func=cmd_start)
    review = sub.add_parser("review")
    review.add_argument("id")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--failed", action="append")
    review.set_defaults(func=cmd_review)
    done = sub.add_parser("done")
    done.add_argument("id")
    done.add_argument("--evidence", action="append", required=True)
    done.set_defaults(func=cmd_done)
    hold = sub.add_parser("hold")
    hold.add_argument("id")
    hold.add_argument("--reason", required=True)
    hold.set_defaults(func=cmd_hold)
    fail = sub.add_parser("fail")
    fail.add_argument("id")
    fail.add_argument(
        "--category",
        required=True,
        choices=sorted(IMMEDIATE_HOLD | {"transient", "timeout", "unknown", "test"}),
    )
    fail.add_argument("--summary", required=True)
    fail.set_defaults(func=cmd_fail)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return args.func(args)
    except ContractError as error:
        print(f"停止: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
