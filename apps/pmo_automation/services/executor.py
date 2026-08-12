"""safe Step executor。

`observe`/`internal_apply` の Step だけを自動実行する。P0 では実外部送信を
一切行わない（`action` は呼び出し側が渡す内部処理専用の関数であり、この
モジュール自体は外部コネクタを一切知らない）。同じ Step を二度成功させず、
失敗は `services.failures` の分類に従って停止・再試行する。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from apps.pmo_automation.models import (
    AutomationLevel,
    ExecutionAttempt,
    ExecutionOutcome,
    FailureCategory,
    WorkStep,
    WorkStepState,
)
from apps.pmo_automation.services import failures

_AUTO_EXECUTABLE_LEVELS = frozenset({AutomationLevel.OBSERVE, AutomationLevel.INTERNAL_APPLY})


class StepNotExecutableError(ValueError):
    """automation_level が observe/internal_apply 以外、または hold 中の Step を
    実行しようとしたことを表す。"""


class StepFailure(Exception):
    """action の失敗を表す。failure_category を必須で持つ。"""

    def __init__(self, category: str, message: str = "") -> None:
        super().__init__(message or category)
        self.category = category
        self.message = message


def execute_step(step: WorkStep, *, action: Callable[[], None], now: datetime) -> ExecutionAttempt | None:
    """Step を一回実行する。

    冪等性: 既に SUCCEEDED の Step は再実行せず None を返す。
    HOLD の Step は人の判断が必要なため実行を拒否する。

    既知の限界（レビュー指摘）: この関数自体は `select_for_update` 等の
    行ロックを取らないため、同一 Step への**同時**呼び出しに対する排他制御は
    呼び出し側の責務である（P0 は `process_pmo_work` のような単一プロセス・
    逐次処理を前提とし、並行呼び出しは想定していない）。
    """

    if step.automation_level not in _AUTO_EXECUTABLE_LEVELS:
        raise StepNotExecutableError(
            f"automation_level={step.automation_level} の Step は自動実行できません（観測/内部反映のみ）。"
        )

    if step.state == WorkStepState.SUCCEEDED:
        return None

    if step.state == WorkStepState.HOLD:
        raise StepNotExecutableError("hold状態のStepは実行できません。再開には人の判断が必要です。")

    started_at = now
    try:
        action()
    except StepFailure as error:
        return _record_failure(
            step, category=error.category, message=error.message, started_at=started_at, now=now
        )
    except Exception as error:  # noqa: BLE001 - 分類不能な失敗は unknown として安全側で扱う
        return _record_failure(
            step, category=FailureCategory.UNKNOWN, message=str(error), started_at=started_at, now=now
        )

    return _record_success(step, started_at=started_at, now=now)


def _record_success(step: WorkStep, *, started_at: datetime, now: datetime) -> ExecutionAttempt:
    step.state = WorkStepState.SUCCEEDED
    step.attempt_count += 1
    step.next_retry_at = None
    step.result_summary = "成功"
    step.save(update_fields=["state", "attempt_count", "next_retry_at", "result_summary", "updated_at"])

    return ExecutionAttempt.objects.create(
        step=step, started_at=started_at, ended_at=now, outcome=ExecutionOutcome.SUCCEEDED
    )


def _record_failure(
    step: WorkStep, *, category: str, message: str, started_at: datetime, now: datetime
) -> ExecutionAttempt:
    # 同一カテゴリの失敗回数は、他カテゴリの失敗と混ぜず ExecutionAttempt から数える
    # （WorkStep.attempt_count はカテゴリ横断の総試行数のため、上限判定には使わない）。
    prior_same_category_failures = step.attempts.filter(
        outcome=ExecutionOutcome.FAILED, failure_category=category
    ).count()
    same_category_attempt_count = prior_same_category_failures + 1

    step.attempt_count += 1

    if failures.should_retry(category, same_category_attempt_count=same_category_attempt_count):
        delay = failures.next_retry_delay_seconds(same_category_attempt_count=same_category_attempt_count)
        step.state = WorkStepState.RETRY_SCHEDULED
        step.next_retry_at = now + timedelta(seconds=delay)
        step.result_summary = f"failure_category={category}、{delay}秒後に再試行予定。"
    else:
        step.state = WorkStepState.HOLD
        step.next_retry_at = None
        step.result_summary = f"failure_category={category} のため停止（人の判断が必要）。"

    step.save(update_fields=["state", "attempt_count", "next_retry_at", "result_summary", "updated_at"])

    return ExecutionAttempt.objects.create(
        step=step,
        started_at=started_at,
        ended_at=now,
        outcome=ExecutionOutcome.FAILED,
        failure_category=category,
        safe_summary=message,
    )
