"""開発エージェント・ハーネス（AH-01〜AH-03）の外部挙動テスト。

ハーネスは Django に依存しないが、`make test` の 1 本のゲートで回帰を捕まえるため
既存のテストランナーから実行する。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from tools.agent_harness.failures import (
    MAX_ATTEMPTS,
    AttemptLog,
    FailureCategory,
    classify_failure,
    next_action,
)
from tools.agent_harness.queue import QUEUE_PATH, QueueError, TicketQueue
from tools.agent_harness.registry import (
    VERIFICATION_REGISTRY,
    checks_for,
    requires_manual_ui,
)
from tools.agent_harness.review import (
    CHECKS,
    SELF_REVIEW,
    ReviewIncomplete,
    ReviewRecord,
)


def _write_queue(tickets: list[dict]) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "queue.json"
    tmp.write_text(json.dumps({"version": 1, "tickets": tickets}, ensure_ascii=False))
    return tmp


def _ticket(ticket_id: str, **overrides) -> dict:
    base = {
        "id": ticket_id,
        "priority": "P0",
        "kind": "harness",
        "depends_on": [],
        "state": "untouched",
        "acceptance": "受入条件",
    }
    base.update(overrides)
    return base


class TicketQueueTests(SimpleTestCase):
    """AH-01: 未完了の最優先チケットと依存を機械が判定できる。"""

    def test_real_queue_loads_and_is_acyclic(self):
        queue = TicketQueue.load(QUEUE_PATH)
        self.assertGreater(len(queue.tickets), 0)
        # LDF は GE の後に来る、という文書の依存順がキューにも入っている。
        self.assertIn("GE-02", queue.get("LDF-01").depends_on)

    def test_next_ticket_prefers_priority_then_id(self):
        path = _write_queue(
            [
                _ticket("B-01", priority="P1"),
                _ticket("A-02", priority="P0"),
                _ticket("A-01", priority="P0"),
            ]
        )
        self.assertEqual(TicketQueue.load(path).next_ticket().id, "A-01")

    def test_next_ticket_resumes_in_progress_first(self):
        path = _write_queue(
            [_ticket("A-01"), _ticket("A-02", state="in_progress")]
        )
        self.assertEqual(TicketQueue.load(path).next_ticket().id, "A-02")

    def test_blocked_ticket_is_not_offered(self):
        path = _write_queue(
            [_ticket("A-01"), _ticket("A-02", depends_on=["A-01"])]
        )
        queue = TicketQueue.load(path)
        self.assertEqual([t.id for t in queue.ready()], ["A-01"])
        self.assertEqual([t.id for t in queue.blocked()], ["A-02"])

    def test_dependency_cycle_is_rejected(self):
        path = _write_queue(
            [
                _ticket("A-01", depends_on=["A-02"]),
                _ticket("A-02", depends_on=["A-01"]),
            ]
        )
        with self.assertRaises(QueueError):
            TicketQueue.load(path)

    def test_unknown_dependency_is_rejected(self):
        path = _write_queue([_ticket("A-01", depends_on=["NOPE"])])
        with self.assertRaises(QueueError):
            TicketQueue.load(path)

    def test_done_requires_evidence(self):
        path = _write_queue([_ticket("A-01")])
        queue = TicketQueue.load(path)
        with self.assertRaises(QueueError):
            queue.update("A-01", state="done")
        updated = queue.update("A-01", state="done", evidence=["test_x"])
        self.assertEqual(updated.get("A-01").state, "done")

    def test_hold_requires_reason(self):
        path = _write_queue([_ticket("A-01")])
        queue = TicketQueue.load(path)
        with self.assertRaises(QueueError):
            queue.update("A-01", state="hold")

    def test_update_does_not_mutate_original(self):
        path = _write_queue([_ticket("A-01")])
        queue = TicketQueue.load(path)
        queue.update("A-01", state="in_progress")
        self.assertEqual(queue.get("A-01").state, "untouched")

    def test_round_trip_save_preserves_state(self):
        path = _write_queue([_ticket("A-01")])
        TicketQueue.load(path).update("A-01", state="done", evidence=["e"]).save(path)
        self.assertEqual(TicketQueue.load(path).get("A-01").state, "done")


class ReviewGateTests(SimpleTestCase):
    """AH-04: テスト合格だけで完了にしない。"""

    def test_all_checks_must_be_answered(self):
        with self.assertRaises(ReviewIncomplete):
            ReviewRecord.build("A-01", "reviewer", {"acceptance": True})

    def test_approved_review_has_no_failures(self):
        record = ReviewRecord.build(
            "A-01", "reviewer", dict.fromkeys([key for key, _ in CHECKS], True)
        )
        self.assertTrue(record.is_approved)
        self.assertIn("承認", record.describe())

    def test_failed_check_blocks_approval(self):
        answers = dict.fromkeys([key for key, _ in CHECKS], True)
        answers["boundary"] = False
        record = ReviewRecord.build("A-01", "reviewer", answers)
        self.assertFalse(record.is_approved)
        self.assertIn("差戻し", record.describe())
        self.assertIn("権限", record.describe())

    def test_rejected_review_cannot_be_marked_done(self):
        path = _write_queue([_ticket("A-01")])
        answers = dict.fromkeys([key for key, _ in CHECKS], True)
        answers["destructive"] = False
        queue = TicketQueue.load(path).update(
            "A-01", review=ReviewRecord.build("A-01", "r", answers).to_dict()
        )
        with self.assertRaises(QueueError):
            queue.update("A-01", state="done", evidence=["test_x"])

    def test_approved_review_allows_done(self):
        path = _write_queue([_ticket("A-01")])
        answers = dict.fromkeys([key for key, _ in CHECKS], True)
        queue = TicketQueue.load(path).update(
            "A-01", review=ReviewRecord.build("A-01", "r", answers).to_dict()
        )
        updated = queue.update("A-01", state="done", evidence=["test_x"])
        self.assertEqual(updated.get("A-01").state, "done")

    def test_review_survives_a_save_and_load(self):
        path = _write_queue([_ticket("A-01")])
        answers = dict.fromkeys([key for key, _ in CHECKS], True)
        TicketQueue.load(path).update(
            "A-01", review=ReviewRecord.build("A-01", "rev", answers).to_dict()
        ).save(path)
        stored = TicketQueue.load(path).get("A-01").review
        self.assertEqual(stored["reviewer"], "rev")

    def test_self_review_is_marked(self):
        answers = dict.fromkeys([key for key, _ in CHECKS], True)
        record = ReviewRecord.build("A-01", SELF_REVIEW, answers)
        self.assertTrue(record.is_self_review)


class VerificationRegistryTests(SimpleTestCase):
    """AH-02: 変更の種類ごとに実行すべき検証が決まっている。"""

    def test_every_queue_kind_is_registered(self):
        kinds = {t.kind for t in TicketQueue.load(QUEUE_PATH).tickets}
        self.assertTrue(kinds <= set(VERIFICATION_REGISTRY), kinds - set(VERIFICATION_REGISTRY))

    def test_model_change_requires_migration_check(self):
        names = {c.name for c in checks_for("model")}
        self.assertIn("migration-consistency", names)
        self.assertIn("permission-boundary", names)

    def test_template_change_requires_manual_ui(self):
        self.assertTrue(requires_manual_ui("template"))
        self.assertFalse(requires_manual_ui("model"))

    def test_unknown_kind_raises(self):
        with self.assertRaises(KeyError):
            checks_for("unknown-kind")


class FailureControlTests(SimpleTestCase):
    """AH-03: 失敗を分類し、3 回で保留する。"""

    def test_classifies_migration_failure(self):
        out = "Your models in app 'projects' have changes that are not yet reflected"
        self.assertEqual(classify_failure(out), FailureCategory.MIGRATION)

    def test_classifies_credential_failure(self):
        self.assertEqual(
            classify_failure("HTTPError: 401 Unauthorized"), FailureCategory.CREDENTIAL
        )

    def test_classifies_ui_failure(self):
        self.assertEqual(
            classify_failure("django.urls.exceptions.NoReverseMatch: 'x' not found"),
            FailureCategory.UI,
        )

    def test_classifies_test_failure(self):
        self.assertEqual(
            classify_failure("FAILED (failures=1)\nAssertionError: 1 != 2"),
            FailureCategory.TEST,
        )

    def test_empty_output_is_unknown(self):
        self.assertEqual(classify_failure("   "), FailureCategory.UNKNOWN)

    def test_third_same_failure_becomes_hold(self):
        log = AttemptLog()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            log = log.record("A-01", FailureCategory.TEST, f"AssertionError {attempt}")
            expected = "hold" if attempt >= MAX_ATTEMPTS else "repair"
            self.assertEqual(next_action(log, "A-01", FailureCategory.TEST), expected)

    def test_different_category_counts_separately(self):
        log = AttemptLog()
        for _ in range(MAX_ATTEMPTS):
            log = log.record("A-01", FailureCategory.TEST, "AssertionError")
        self.assertEqual(next_action(log, "A-01", FailureCategory.LINT), "repair")

    def test_credential_failure_is_never_retried(self):
        log = AttemptLog().record("A-01", FailureCategory.CREDENTIAL, "401")
        self.assertEqual(next_action(log, "A-01", FailureCategory.CREDENTIAL), "hold")

    def test_record_is_append_only(self):
        log = AttemptLog()
        log.record("A-01", FailureCategory.TEST, "x")
        self.assertEqual(log.entries, ())

    def test_clear_removes_only_that_ticket(self):
        log = (
            AttemptLog()
            .record("A-01", FailureCategory.TEST, "x")
            .record("A-02", FailureCategory.TEST, "y")
            .clear("A-01")
        )
        self.assertEqual([e["ticket"] for e in log.entries], ["A-02"])

    def test_evidence_is_truncated_to_one_line(self):
        log = AttemptLog().record("A-01", FailureCategory.TEST, "\n\nfirst line\nsecond")
        self.assertEqual(log.entries[0]["evidence"], "first line")
