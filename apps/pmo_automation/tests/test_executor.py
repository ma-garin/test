"""services/executor.py・failures.py と、H-05/H-06/H-09/H-10 を検証する。"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_automation.models import (
    AutomationLevel,
    ExecutionAttempt,
    ExecutionOutcome,
    FailureCategory,
    PmoWorkItem,
    WorkItemState,
    WorkKind,
    WorkPlan,
    WorkStep,
    WorkStepState,
)
from apps.pmo_automation.services import executor
from apps.projects.models import Project

NOW = timezone.now()


class ExecutorTestBase(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        self.work_item = PmoWorkItem.objects.create(
            tenant=self.tenant,
            project=self.project,
            kind=WorkKind.DETECTION_TRIAGE,
            source_type="alert",
            source_key="alert-1",
            dedupe_key="alert:alert-1",
        )
        self.plan = WorkPlan.objects.create(
            work_item=self.work_item, version=1, automation_level=AutomationLevel.INTERNAL_APPLY
        )

    def _step(self, **kwargs) -> WorkStep:
        defaults = {
            "plan": self.plan,
            "order": 1,
            "kind": "internal_draft",
            "automation_level": AutomationLevel.INTERNAL_APPLY,
            "idempotency_key": "step-1",
        }
        defaults.update(kwargs)

        return WorkStep.objects.create(**defaults)


class IdempotencyTests(ExecutorTestBase):
    def test_H05_同じStepを二回processしても成功は一件だけ(self) -> None:
        """H-05: internal_apply の下書き作成 Step を二回 process する。
        下書き（=actionの呼出）は一件、成功 Attempt は一件。外部コネクタ呼出は0
        （action自体が外部通信を一切しない設計のため自明に満たす）。"""

        step = self._step()
        call_count = 0

        def create_draft() -> None:
            nonlocal call_count
            call_count += 1

        first = executor.execute_step(step, action=create_draft, now=NOW)
        step.refresh_from_db()
        second = executor.execute_step(step, action=create_draft, now=NOW)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(call_count, 1)
        self.assertEqual(ExecutionAttempt.objects.filter(outcome=ExecutionOutcome.SUCCEEDED).count(), 1)
        self.assertEqual(step.state, WorkStepState.SUCCEEDED)

    def test_成功したStepはstateがSUCCEEDEDになる(self) -> None:
        step = self._step()

        executor.execute_step(step, action=lambda: None, now=NOW)

        step.refresh_from_db()
        self.assertEqual(step.state, WorkStepState.SUCCEEDED)
        self.assertEqual(step.attempt_count, 1)


class AutomationLevelGuardTests(ExecutorTestBase):
    def test_H06_approveレベルのStepは実行されない(self) -> None:
        """H-06: approve の外部更新 Step が未承認。process しても実行されず、
        Work Item は awaiting_approval のまま。fake connector 呼出回数は0。"""

        self.work_item.state = WorkItemState.AWAITING_APPROVAL
        self.work_item.save(update_fields=["state"])
        step = self._step(automation_level=AutomationLevel.APPROVE, kind="external_notify")
        call_count = 0

        def send_external() -> None:
            nonlocal call_count
            call_count += 1

        with self.assertRaises(executor.StepNotExecutableError):
            executor.execute_step(step, action=send_external, now=NOW)

        self.work_item.refresh_from_db()
        self.assertEqual(call_count, 0)
        self.assertEqual(self.work_item.state, WorkItemState.AWAITING_APPROVAL)

    def test_confirmレベルのStepも実行されない(self) -> None:
        step = self._step(automation_level=AutomationLevel.CONFIRM)

        with self.assertRaises(executor.StepNotExecutableError):
            executor.execute_step(step, action=lambda: None, now=NOW)

    def test_prohibitedレベルのStepも実行されない(self) -> None:
        step = self._step(automation_level=AutomationLevel.PROHIBITED)

        with self.assertRaises(executor.StepNotExecutableError):
            executor.execute_step(step, action=lambda: None, now=NOW)


class RetryPolicyTests(ExecutorTestBase):
    def _fail_with(self, category: str):
        def action() -> None:
            raise executor.StepFailure(category, "失敗しました")

        return action

    def test_H09_一時失敗は三回目でholdになり四回目は呼ばれない(self) -> None:
        """H-09: 同じStepがtransient errorを返す。三回失敗すると三回目でhold、
        次回再試行は作られない。四回目のexecutor呼出は発生しない。"""

        step = self._step()
        action = self._fail_with(FailureCategory.TRANSIENT)

        first = executor.execute_step(step, action=action, now=NOW)
        step.refresh_from_db()
        state_after_first = step.state
        next_retry_after_first = step.next_retry_at

        second = executor.execute_step(step, action=action, now=NOW + timedelta(minutes=1))
        step.refresh_from_db()
        state_after_second = step.state

        third = executor.execute_step(step, action=action, now=NOW + timedelta(minutes=2))
        step.refresh_from_db()
        state_after_third = step.state

        self.assertEqual(first.outcome, ExecutionOutcome.FAILED)
        self.assertEqual(state_after_first, WorkStepState.RETRY_SCHEDULED)
        self.assertIsNotNone(next_retry_after_first)

        self.assertEqual(second.outcome, ExecutionOutcome.FAILED)
        self.assertEqual(state_after_second, WorkStepState.RETRY_SCHEDULED)

        self.assertEqual(third.outcome, ExecutionOutcome.FAILED)
        self.assertEqual(state_after_third, WorkStepState.HOLD)
        self.assertIsNone(step.next_retry_at)

        # 四回目: holdになったStepは実行を拒否する（=呼出そのものが安全に止まる）。
        with self.assertRaises(executor.StepNotExecutableError):
            executor.execute_step(step, action=action, now=NOW + timedelta(minutes=3))
        self.assertEqual(
            ExecutionAttempt.objects.filter(step=step, outcome=ExecutionOutcome.FAILED).count(), 3
        )

    def test_H10_credential失敗は一回目でholdになり再試行予約は作られない(self) -> None:
        """H-10: Step が credential error を返す。一回実行すると一回目でhold、
        再試行予約は作られない。"""

        step = self._step()
        action = self._fail_with(FailureCategory.CREDENTIAL)

        attempt = executor.execute_step(step, action=action, now=NOW)

        step.refresh_from_db()
        self.assertEqual(attempt.failure_category, FailureCategory.CREDENTIAL)
        self.assertEqual(step.state, WorkStepState.HOLD)
        self.assertIsNone(step.next_retry_at)
        self.assertEqual(step.attempt_count, 1)

    def test_permission失敗も即座にholdになる(self) -> None:
        step = self._step()
        action = self._fail_with(FailureCategory.PERMISSION)

        executor.execute_step(step, action=action, now=NOW)

        step.refresh_from_db()
        self.assertEqual(step.state, WorkStepState.HOLD)

    def test_policy失敗も即座にholdになる(self) -> None:
        step = self._step()
        action = self._fail_with(FailureCategory.POLICY)

        executor.execute_step(step, action=action, now=NOW)

        step.refresh_from_db()
        self.assertEqual(step.state, WorkStepState.HOLD)
        self.assertIsNone(step.next_retry_at)

    def test_secrets失敗も即座にholdになる(self) -> None:
        step = self._step()
        action = self._fail_with(FailureCategory.SECRETS)

        executor.execute_step(step, action=action, now=NOW)

        step.refresh_from_db()
        self.assertEqual(step.state, WorkStepState.HOLD)
        self.assertIsNone(step.next_retry_at)

    def test_分類できない例外はunknownとして再試行対象になる(self) -> None:
        step = self._step()

        def action() -> None:
            raise RuntimeError("想定外のエラー")

        attempt = executor.execute_step(step, action=action, now=NOW)

        step.refresh_from_db()
        self.assertEqual(attempt.failure_category, FailureCategory.UNKNOWN)
        self.assertEqual(step.state, WorkStepState.RETRY_SCHEDULED)
