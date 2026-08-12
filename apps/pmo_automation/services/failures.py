"""失敗分類と再試行判断。

`docs/agent/pmo_autopilot_contract.json` の `failure_policy` と一対一で
対応する。credential/permission/policy/secrets は即座に停止し、絶対に
再試行しない（forbidden_actions: credential失敗の再試行）。
"""

from __future__ import annotations

from apps.pmo_automation.models import FailureCategory

IMMEDIATE_HOLD_CATEGORIES: frozenset[str] = frozenset(
    {
        FailureCategory.CREDENTIAL,
        FailureCategory.PERMISSION,
        FailureCategory.POLICY,
        FailureCategory.SECRETS,
    }
)
RETRYABLE_CATEGORIES: frozenset[str] = frozenset(
    {
        FailureCategory.TRANSIENT,
        FailureCategory.TIMEOUT,
        FailureCategory.UNKNOWN,
        FailureCategory.TEST,
    }
)
MAX_SAME_CATEGORY_ATTEMPTS = 3
RETRY_SCHEDULE_SECONDS: tuple[int, ...] = (60, 300, 1800)


def is_immediate_hold(category: str) -> bool:
    return category in IMMEDIATE_HOLD_CATEGORIES


def is_retryable(category: str) -> bool:
    return category in RETRYABLE_CATEGORIES


def should_retry(category: str, *, same_category_attempt_count: int) -> bool:
    """同一カテゴリの失敗回数（今回の失敗を含む）から再試行してよいかを判定する。

    immediate_hold_categories は絶対に再試行しない。retryable_categories でも
    上限（3回）に達したら再試行しない。未知のカテゴリは安全側で再試行しない。
    """

    if is_immediate_hold(category):
        return False
    if not is_retryable(category):
        return False

    return same_category_attempt_count < MAX_SAME_CATEGORY_ATTEMPTS


def next_retry_delay_seconds(*, same_category_attempt_count: int) -> int:
    """同一カテゴリの失敗回数に応じた再試行までの待機秒数。"""

    index = max(0, min(same_category_attempt_count - 1, len(RETRY_SCHEDULE_SECONDS) - 1))
    return RETRY_SCHEDULE_SECONDS[index]
