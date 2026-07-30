"""通知ルール — 「送れる」ではなく「何を送るか」。

送信そのものはコネクタの仕事。ここが持つのは判断で、実務で効くのは次の 2 点。

1. **同じ対象を繰り返し通知しない。** `NotificationLog.trigger` を対象ごとの
   一意キーにして、送信済みなら二度と送らない。毎朝同じ遅延タスクが流れる
   通知は、3 日で読まれなくなる。
2. **しきい値を設ける。** 軽微なものは送らない（`notify_rules.py`）。

一覧でまとめる通知（承認待ち・期限超過）は、**まだ通知していない対象だけ**を
本文に載せる。既に知らせた行を毎回載せると、新しい 1 件が埋もれる。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from django.utils import timezone

from apps.dashboard.models import InterventionProposal
from apps.integrations.models import Connection, NotificationLog, Provider
from apps.integrations.notification_selectors import (
    critical_alerts,
    notify_connections,
    overdue_tasks,
    proposals_awaiting_decision,
    sent_dedupe_keys,
    stale_change_requests,
    stale_proposals,
)
from apps.integrations.services.connectors.base import BaseConnector, ConnectorError
from apps.integrations.services.connectors.slack import SlackConnector
from apps.integrations.services.connectors.teams import TeamsConnector
from apps.integrations.services.notify_rules import (
    ALERT_RESPONSE_SLA_DAYS,
    MAX_DIGEST_ITEMS,
    MIN_OVERDUE_DAYS,
    MIN_PROPOSAL_CONFIDENCE,
    Trigger,
    dedupe_key,
)

#: 通知を送れるコネクタ。取込側の `get_connector()` とは分けている。
#: 通知経路に課題取込用のコネクタが紛れ込むと、送信時に初めて失敗する。
_NOTIFY_CONNECTORS: dict[str, type[BaseConnector]] = {
    Provider.SLACK: SlackConnector,
    Provider.TEAMS: TeamsConnector,
}


def notify_connector(connection: Connection) -> BaseConnector:
    """接続に対応する通知コネクタを返す。"""

    connector_class = _NOTIFY_CONNECTORS.get(connection.provider)

    if connector_class is None:
        raise ConnectorError(f"{connection.get_provider_display()} は通知に対応していません")

    return connector_class(connection)


# ── 通知の形 ────────────────────────────────────────────────


@dataclass(frozen=True)
class PendingNotification:
    """送る候補。まだ抑止判定を通っていない。

    `keys` と `lines` は同じ並び。抑止で落ちた行は本文から外れる。
    """

    kind: str
    subject: str
    header: str
    keys: tuple[str, ...]
    lines: tuple[str, ...]
    project_id: object | None = None
    digest: bool = False

    def title(self, count: int) -> str:
        if self.digest:
            return f"{self.subject}（{count}件）"

        return self.subject

    def body(self, indexes: tuple[int, ...]) -> str:
        parts = [self.header] if self.header else []
        parts.extend(self.lines[i] for i in indexes)

        return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class NotifySummary:
    """実行結果。件数を分けて持つのは、0 件の理由を区別するため。"""

    connections: int = 0
    sent: int = 0
    suppressed: int = 0
    failed: int = 0
    candidates: int = 0

    def merged(self, **counts: int) -> NotifySummary:
        """不変のまま加算する。"""

        return replace(self, **{key: getattr(self, key) + value for key, value in counts.items()})


def _stamp(value: datetime) -> str:
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")


# ── 契機ごとの組み立て ──────────────────────────────────────


def build_alert_notifications(tenant) -> tuple[PendingNotification, ...]:
    """重大アラートの検知。案件名・内容・検知時刻・対応期限を載せる。"""

    notifications: list[PendingNotification] = []

    for alert in critical_alerts(tenant):
        # Alert は期限を持たないので、SLA から一次対応期限を導く。
        # 「いつまでに」が無い通知は読んでも行動が変わらない。
        due = alert.detected_at + timedelta(days=ALERT_RESPONSE_SLA_DAYS)
        detail = alert.detail.strip()

        line = "\n".join(
            part
            for part in (
                f"内容: {alert.title}",
                detail,
                f"分類: {alert.get_category_display()}",
                f"検知時刻: {_stamp(alert.detected_at)}",
                f"対応期限: {_stamp(due)}（検知から{ALERT_RESPONSE_SLA_DAYS}日）",
            )
            if part
        )

        notifications.append(
            PendingNotification(
                kind=Trigger.ALERT,
                subject=f"[重大アラート] {alert.project.name} / {alert.title}",
                header=f"案件: {alert.project.name}",
                keys=(dedupe_key(Trigger.ALERT, alert.pk),),
                lines=(line,),
                project_id=alert.project_id,
            )
        )

    return tuple(notifications)


def build_proposal_notifications(tenant) -> tuple[PendingNotification, ...]:
    """AI 介入提案の作成。提案・根拠・信頼度・判断が必要であることを載せる。"""

    notifications: list[PendingNotification] = []

    for proposal in proposals_awaiting_decision(tenant):
        if not _is_confident_enough(proposal):
            continue

        line = "\n".join(
            part
            for part in (
                f"提案: {proposal.title}",
                f"根拠: {proposal.rationale.strip()}" if proposal.rationale.strip() else "",
                f"推奨アクション: {proposal.recommended_action}"
                if proposal.recommended_action
                else "",
                f"信頼度: {_confidence_label(proposal.confidence)}",
                "※ これは AI の提案です。採用・修正・不採用の判断が必要です。",
            )
            if part
        )

        notifications.append(
            PendingNotification(
                kind=Trigger.PROPOSAL,
                subject=f"[判断が必要] {proposal.project.name} / {proposal.title}",
                header=f"案件: {proposal.project.name}",
                keys=(dedupe_key(Trigger.PROPOSAL, proposal.pk),),
                lines=(line,),
                project_id=proposal.project_id,
            )
        )

    return tuple(notifications)


def _is_confident_enough(proposal: InterventionProposal) -> bool:
    """信頼度によるふるい落とし。

    null は「AI 未使用のルールベース提案」で、当て推量ではないため対象に含める。
    低信頼度の提案は、人を呼ぶ前に根拠を足すべき段階なので送らない。
    """

    if proposal.confidence is None:
        return True

    return proposal.confidence >= MIN_PROPOSAL_CONFIDENCE


def _confidence_label(confidence: float | None) -> str:
    if confidence is None:
        return "未算出（ルールベース提案）"

    return f"{confidence:.0%}"


def build_stale_approval_notifications(
    tenant, *, now: datetime
) -> tuple[PendingNotification, ...]:
    """承認待ちの滞留。案件ごとに 1 通の一覧へまとめる。

    1 件ずつ送ると、滞留している案件ほど通知が増えて読まれなくなる。
    """

    buckets: dict[object, list[tuple[str, str, object]]] = {}
    names: dict[object, str] = {}

    for proposal in stale_proposals(tenant, now=now):
        days = (now - proposal.created_at).days
        buckets.setdefault(proposal.project_id, []).append(
            (
                dedupe_key(Trigger.STALE, proposal.pk),
                f"・AI介入提案 / {proposal.title}（{days}日 未判断）",
                proposal.created_at,
            )
        )
        names[proposal.project_id] = proposal.project.name

    for change in stale_change_requests(tenant, now=now):
        days = (now - change.created_at).days
        buckets.setdefault(change.project_id, []).append(
            (
                dedupe_key(Trigger.STALE, change.pk),
                f"・変更要求 / {change.title}（{days}日 未判断）",
                change.created_at,
            )
        )
        names[change.project_id] = change.project.name

    return _build_digests(
        buckets=buckets,
        names=names,
        kind=Trigger.STALE,
        subject_template="[承認待ち滞留] {project} 判断されていない案件があります",
    )


def build_overdue_task_notifications(
    tenant, *, today, min_overdue_days: int = MIN_OVERDUE_DAYS
) -> tuple[PendingNotification, ...]:
    """期限超過タスクの発生。タスク名・担当・超過日数を載せる。"""

    buckets: dict[object, list[tuple[str, str, object]]] = {}
    names: dict[object, str] = {}

    for task in overdue_tasks(tenant, today=today, min_overdue_days=min_overdue_days):
        overdue_days = (today - task.planned_end).days
        owner = task.owner or "担当未設定"
        buckets.setdefault(task.project_id, []).append(
            (
                dedupe_key(Trigger.OVERDUE, task.pk),
                f"・{task.wbs_code} {task.name}（担当: {owner} / {overdue_days}日超過）",
                task.planned_end,
            )
        )
        names[task.project_id] = task.project.name

    return _build_digests(
        buckets=buckets,
        names=names,
        kind=Trigger.OVERDUE,
        subject_template="[期限超過] {project} 期限を過ぎたタスクがあります",
    )


def _build_digests(
    *,
    buckets: dict[object, list[tuple[str, str, object]]],
    names: dict[object, str],
    kind: str,
    subject_template: str,
) -> tuple[PendingNotification, ...]:
    """案件ごとの一覧通知を作る。古いものから順に、上限まで載せる。"""

    notifications: list[PendingNotification] = []

    for project_id, rows in buckets.items():
        ordered = sorted(rows, key=lambda row: row[2])[:MAX_DIGEST_ITEMS]
        project_name = names[project_id]

        notifications.append(
            PendingNotification(
                kind=kind,
                subject=subject_template.format(project=project_name),
                header=f"案件: {project_name}",
                keys=tuple(row[0] for row in ordered),
                lines=tuple(row[1] for row in ordered),
                project_id=project_id,
                digest=True,
            )
        )

    return tuple(notifications)


def collect_notifications(tenant, *, now: datetime | None = None) -> tuple[PendingNotification, ...]:
    """全契機の候補を集める。抑止判定は送信時に行う。"""

    moment = now or timezone.now()
    today = timezone.localtime(moment).date()

    return (
        *build_alert_notifications(tenant),
        *build_proposal_notifications(tenant),
        *build_stale_approval_notifications(tenant, now=moment),
        *build_overdue_task_notifications(tenant, today=today),
    )


# ── 送信 ────────────────────────────────────────────────────


def _targets(connection: Connection, notifications: tuple[PendingNotification, ...]):
    """接続が受け持つ通知だけを返す。

    案件を指定した接続はその案件だけ、未指定ならテナント全体を受け持つ。
    """

    if connection.project_id is None:
        return notifications

    return tuple(note for note in notifications if note.project_id == connection.project_id)


def send_pending_notifications(
    tenant,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    connections: list[Connection] | None = None,
) -> NotifySummary:
    """未通知のものだけを送る。

    モックモードなら実送信せず履歴だけが残るので、鍵が無くても経路を検証できる。
    """

    moment = now or timezone.now()
    notifications = collect_notifications(tenant, now=moment)
    targets = notify_connections(tenant) if connections is None else connections

    summary = NotifySummary(connections=len(targets), candidates=len(notifications))

    for connection in targets:
        connector = notify_connector(connection)

        for note in _targets(connection, notifications):
            summary = _deliver(
                connection=connection,
                connector=connector,
                note=note,
                summary=summary,
                dry_run=dry_run,
            )

    return summary


def _deliver(
    *,
    connection: Connection,
    connector: BaseConnector,
    note: PendingNotification,
    summary: NotifySummary,
    dry_run: bool,
) -> NotifySummary:
    already = sent_dedupe_keys(connection, note.keys)
    fresh = tuple(index for index, key in enumerate(note.keys) if key not in already)

    if not fresh:
        # 全対象が通知済み。履歴も残さない（毎回 SKIPPED を積むと表が膨らむだけ）。
        return summary.merged(suppressed=1)

    if dry_run:
        return summary.merged(suppressed=0)

    fresh_keys = tuple(note.keys[index] for index in fresh)
    result = connector.send(
        title=note.title(len(fresh)),
        body=note.body(fresh),
        trigger=fresh_keys[0],
    )

    if not result.ok:
        return summary.merged(failed=1)

    _record_extra_keys(connection=connection, keys=fresh_keys[1:], title=note.title(len(fresh)))

    return summary.merged(sent=1)


def _record_extra_keys(*, connection: Connection, keys: tuple[str, ...], title: str) -> None:
    """一覧通知に含めた 2 件目以降の抑止キーを履歴へ残す。

    `NotificationLog` は「1 送信 = 1 行」だが、抑止は対象単位でないと成立しない。
    行を残さないと、次回また同じ対象が一覧に混ざる。本文は 1 行目に載っているので
    ここでは重複させず、どの通知に含めたかだけを件名で辿れるようにする。
    """

    if not keys:
        return

    NotificationLog.objects.bulk_create(
        [
            NotificationLog(
                connection=connection,
                title=title[:300],
                body="",
                status=NotificationLog.Status.SENT,
                sent_at=timezone.now(),
                trigger=key[:64],
                error="",
                channel=str(connection.config.get("channel", "") or "")[:200],
            )
            for key in keys
        ]
    )


__all__ = [
    "NotifySummary",
    "PendingNotification",
    "build_alert_notifications",
    "build_overdue_task_notifications",
    "build_proposal_notifications",
    "build_stale_approval_notifications",
    "collect_notifications",
    "notify_connector",
    "send_pending_notifications",
]
