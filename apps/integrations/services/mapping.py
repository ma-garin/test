"""外部の状態・優先度を、内部の選択肢へ写す対応表。

Jira の "In Progress"、Redmine の "進行中" は、どちらも内部では
`Issue.Status.IN_PROGRESS` にしたい。この変換をビューや取込処理へ散らすと、
連携先が増えるたびに if が増え、どこで落ちたのか追えなくなる。

**対応表に無い値を黙って捨てない。** 既定値へ落としたことを呼び出し側へ返し、
同期履歴（`SyncJob.detail`）に残す。取り込めていない状態が静かに増えるのが、
この種の連携で最も見つけにくい壊れ方であるため。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.projects.models import Issue, Severity

#: 対応表に無かったときの既定値。「未対応」に寄せて、見落としを取りこぼさない。
DEFAULT_STATUS: str = Issue.Status.OPEN

#: 重大度の既定値。低く見積もると対応漏れになるため中位に置く。
DEFAULT_SEVERITY: str = Severity.MEDIUM


@dataclass(frozen=True)
class Mapped:
    """変換結果。`matched` が False なら既定値へ落ちたことを意味する。"""

    value: str
    matched: bool
    raw: str = ""


#: 状態の対応表。Jira / Redmine / 日本語表記を同じ表で吸収する。
STATUS_MAP: dict[str, str] = {
    # 未対応
    "open": Issue.Status.OPEN,
    "to do": Issue.Status.OPEN,
    "todo": Issue.Status.OPEN,
    "backlog": Issue.Status.OPEN,
    "new": Issue.Status.OPEN,
    "未対応": Issue.Status.OPEN,
    "新規": Issue.Status.OPEN,
    # 対応中
    "in progress": Issue.Status.IN_PROGRESS,
    "in review": Issue.Status.IN_PROGRESS,
    "doing": Issue.Status.IN_PROGRESS,
    "進行中": Issue.Status.IN_PROGRESS,
    "対応中": Issue.Status.IN_PROGRESS,
    # ブロック中
    "blocked": Issue.Status.BLOCKED,
    "on hold": Issue.Status.BLOCKED,
    "waiting": Issue.Status.BLOCKED,
    "feedback": Issue.Status.BLOCKED,
    "保留": Issue.Status.BLOCKED,
    # 解決
    "resolved": Issue.Status.RESOLVED,
    "fixed": Issue.Status.RESOLVED,
    "解決": Issue.Status.RESOLVED,
    # 完了
    "closed": Issue.Status.CLOSED,
    "done": Issue.Status.CLOSED,
    "rejected": Issue.Status.CLOSED,
    "完了": Issue.Status.CLOSED,
    "終了": Issue.Status.CLOSED,
}

#: 優先度 → 重大度の対応表。Jira の Priority と Redmine の優先度を同じ表で扱う。
SEVERITY_MAP: dict[str, str] = {
    "lowest": Severity.LOW,
    "low": Severity.LOW,
    "trivial": Severity.LOW,
    "minor": Severity.LOW,
    "低": Severity.LOW,
    "medium": Severity.MEDIUM,
    "normal": Severity.MEDIUM,
    "major": Severity.MEDIUM,
    "通常": Severity.MEDIUM,
    "中": Severity.MEDIUM,
    "high": Severity.HIGH,
    "urgent": Severity.HIGH,
    "急いで": Severity.HIGH,
    "高": Severity.HIGH,
    "highest": Severity.CRITICAL,
    "critical": Severity.CRITICAL,
    "blocker": Severity.CRITICAL,
    "immediate": Severity.CRITICAL,
    "重大": Severity.CRITICAL,
    "最優先": Severity.CRITICAL,
}


def normalize(raw: str | None) -> str:
    """表記ゆれを吸収する。`In_Progress` `IN PROGRESS` `in-progress` を同じ鍵にする。"""

    text = str(raw or "").replace("_", " ").replace("-", " ")

    return " ".join(text.split()).lower()


def _map(raw: str | None, table: dict[str, str], default: str) -> Mapped:
    """共通の変換。空欄は「未指定」であって未知ではないので、記録の対象にしない。"""

    key = normalize(raw)

    if not key:
        # 外部側で値が無いだけ。既定値で埋めるのが正しく、警告する必要はない。
        return Mapped(value=default, matched=True, raw="")

    value = table.get(key)

    if value is None:
        return Mapped(value=default, matched=False, raw=str(raw))

    return Mapped(value=value, matched=True, raw=str(raw))


def map_status(raw: str | None) -> Mapped:
    """外部の状態を `Issue.Status` へ写す。"""

    return _map(raw, STATUS_MAP, DEFAULT_STATUS)


def map_severity(raw: str | None) -> Mapped:
    """外部の優先度を `Severity` へ写す。"""

    return _map(raw, SEVERITY_MAP, DEFAULT_SEVERITY)
