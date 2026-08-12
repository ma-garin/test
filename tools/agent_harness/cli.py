"""ハーネスのコマンドライン入口。

    python -m tools.agent_harness.cli next          # 次に着手するチケット
    python -m tools.agent_harness.cli status        # 状態の内訳と保留一覧
    python -m tools.agent_harness.cli checks <kind> # その種別で走らせる検証
    python -m tools.agent_harness.cli start <id>
    python -m tools.agent_harness.cli done <id> --evidence "テスト名"
    python -m tools.agent_harness.cli hold <id> --reason "理由"
    python -m tools.agent_harness.cli fail <id> --log <file>   # 失敗を分類し次の行動を返す

副作用のあるコマンド（start/done/hold/fail）だけが `docs/agent/queue.json` を書き換える。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.agent_harness.failures import AttemptLog, classify_failure, next_action
from tools.agent_harness.queue import TicketQueue
from tools.agent_harness.registry import MANUAL_UI_CHECKS, checks_for, requires_manual_ui
from tools.agent_harness.review import CHECKS, ReviewRecord


def _print_ticket(queue: TicketQueue, ticket_id: str) -> None:
    ticket = queue.get(ticket_id)
    print(f"{ticket.id} [{ticket.priority}/{ticket.kind}] {ticket.state}")
    print(f"  受入条件: {ticket.acceptance}")
    if ticket.depends_on:
        print(f"  依存: {', '.join(ticket.depends_on)}")
    if ticket.evidence:
        print(f"  証跡: {'; '.join(ticket.evidence)}")
    if ticket.hold_reason:
        print(f"  保留理由: {ticket.hold_reason}")


def cmd_next(_: argparse.Namespace) -> int:
    queue = TicketQueue.load()
    ticket = queue.next_ticket()
    if ticket is None:
        print("依存解除済みの未完了チケットはありません。")
        return 0
    _print_ticket(queue, ticket.id)
    print("\n  検証（AH-02）:")
    for check in checks_for(ticket.kind):
        print(f"    - {check.name}: {check.command}")
    if requires_manual_ui(ticket.kind):
        print("  実機確認（コマンドで代替不可）:")
        for item in MANUAL_UI_CHECKS:
            print(f"    - {item}")
    attempts = AttemptLog.load()
    last = attempts.last_for(ticket.id)
    if last:
        print(f"\n  直前の失敗: [{last['category']}] {last['evidence']}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    queue = TicketQueue.load()
    counts = queue.summary()
    print("状態: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    ready = queue.ready()
    print(f"着手可能: {len(ready)} 件 → {', '.join(t.id for t in ready[:8])}")
    holds = [t for t in queue.tickets if t.state in ("hold", "decision_required")]
    if holds:
        print("保留・要判断:")
        for ticket in holds:
            print(f"  {ticket.id}: {ticket.hold_reason or ticket.acceptance}")
    return 0


def cmd_checks(args: argparse.Namespace) -> int:
    for check in checks_for(args.kind):
        print(check.command)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    queue = TicketQueue.load()
    ticket = queue.get(args.id)
    if not queue.is_unblocked(ticket):
        blocking = [d for d in ticket.depends_on if queue.get(d).state != "done"]
        print(f"{args.id} は依存未解決です: {', '.join(blocking)}", file=sys.stderr)
        return 1
    queue.update(args.id, state="in_progress").save()
    _print_ticket(TicketQueue.load(), args.id)
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    queue = TicketQueue.load().update(args.id, state="done", evidence=args.evidence)
    queue.save()
    AttemptLog.load().clear(args.id).save()
    _print_ticket(queue, args.id)
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    queue = TicketQueue.load().update(args.id, state="hold", hold_reason=args.reason)
    queue.save()
    _print_ticket(queue, args.id)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """AH-04: 実装とは別の工程としてのレビューを記録する。"""

    failed = set(args.failed or ())
    answers = {key: key not in failed for key, _ in CHECKS}
    record = ReviewRecord.build(args.id, args.reviewer, answers, note=args.note or "")
    TicketQueue.load().update(args.id, review=record.to_dict()).save()
    print(record.describe())
    if not record.is_approved:
        print("  差戻しのため done にできません。指摘を直してから再レビューしてください。")
    return 0


def cmd_checklist(_: argparse.Namespace) -> int:
    for key, label in CHECKS:
        print(f"{key}: {label}")
    return 0


def cmd_fail(args: argparse.Namespace) -> int:
    output = Path(args.log).read_text(encoding="utf-8") if args.log else sys.stdin.read()
    category = classify_failure(output)
    attempts = AttemptLog.load().record(args.id, category, output)
    attempts.save()
    action = next_action(attempts, args.id, category)
    count = attempts.count(args.id, category)
    print(f"分類: {category.value}（{args.id} で {count} 回目）→ {action}")
    if action == "hold":
        print("  同一失敗の上限、または人の判断が必要な分類です。保留して次の依存解除済みへ進む。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_harness", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("next", help="次に着手するチケット").set_defaults(func=cmd_next)
    sub.add_parser("status", help="状態の内訳").set_defaults(func=cmd_status)

    p_checks = sub.add_parser("checks", help="種別ごとの検証コマンド")
    p_checks.add_argument("kind")
    p_checks.set_defaults(func=cmd_checks)

    p_start = sub.add_parser("start", help="着手を記録")
    p_start.add_argument("id")
    p_start.set_defaults(func=cmd_start)

    p_done = sub.add_parser("done", help="完了を証跡つきで記録")
    p_done.add_argument("id")
    p_done.add_argument("--evidence", action="append", required=True)
    p_done.set_defaults(func=cmd_done)

    p_hold = sub.add_parser("hold", help="保留を理由つきで記録")
    p_hold.add_argument("id")
    p_hold.add_argument("--reason", required=True)
    p_hold.set_defaults(func=cmd_hold)

    sub.add_parser("checklist", help="AH-04 のレビュー項目").set_defaults(func=cmd_checklist)

    p_review = sub.add_parser("review", help="レビュー結果を記録（AH-04）")
    p_review.add_argument("id")
    p_review.add_argument("--reviewer", required=True)
    p_review.add_argument(
        "--failed",
        action="append",
        help="落ちた項目のキー（複数可）。省略すると全項目を承認扱いにする。",
    )
    p_review.add_argument("--note")
    p_review.set_defaults(func=cmd_review)

    p_fail = sub.add_parser("fail", help="失敗を分類して次の行動を返す")
    p_fail.add_argument("id")
    p_fail.add_argument("--log", help="コマンド出力のファイル。省略時は標準入力。")
    p_fail.set_defaults(func=cmd_fail)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
