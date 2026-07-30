"""通知対象の参照。

ここには「取り出す」だけを置き、送るかどうかの判断は
`services/notify.py` に置く。テナント単位でまとめて引くのは、案件ごとに
ループして問い合わせると案件数ぶんクエリが増えるため。

なお、同期側（Jira / Redmine 取込）の参照と衝突しないよう、
通知専用のモジュールとして分けている。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import Q, QuerySet

from apps.dashboard.models import Alert, InterventionProposal
from apps.integrations.models import NOTIFY_PROVIDERS, Connection, NotificationLog
from apps.integrations.services.notify_rules import (
    NOTIFY_ALERT_SEVERITIES,
    STALE_APPROVAL_DAYS,
)
from apps.projects.models import ChangeRequest, WbsTask


def notify_connections(tenant) -> list[Connection]:
    """テナントの有効な通知先。案件限定の接続もここに含まれる。"""

    return list(
        Connection.objects.filter(
            tenant=tenant, is_active=True, provider__in=NOTIFY_PROVIDERS
        ).order_by("provider", "name")
    )


def critical_alerts(tenant) -> QuerySet[Alert]:
    """未対応の重大アラート。確認済み・解消済みは通知しない。"""

    return (
        Alert.objects.select_related("project")
        .filter(
            project__tenant=tenant,
            project__deleted_at__isnull=True,
            status=Alert.Status.OPEN,
            severity__in=NOTIFY_ALERT_SEVERITIES,
        )
        .order_by("-detected_at")
    )


def proposals_awaiting_decision(tenant) -> QuerySet[InterventionProposal]:
    """まだ人が判断していない介入提案。"""

    return (
        InterventionProposal.objects.select_related("project")
        .filter(
            project__tenant=tenant,
            project__deleted_at__isnull=True,
            status=InterventionProposal.Status.PROPOSED,
        )
        .order_by("-created_at")
    )


def stale_proposals(tenant, *, now: datetime) -> QuerySet[InterventionProposal]:
    """一定期間、採否が決まっていない介入提案。

    滞留の起点は起票時刻。更新でリセットすると、体裁だけ直して放置する
    運用を助長するため、`updated_at` は使わない。
    """

    threshold = now - timedelta(days=STALE_APPROVAL_DAYS)

    return proposals_awaiting_decision(tenant).filter(created_at__lte=threshold)


def stale_change_requests(tenant, *, now: datetime) -> QuerySet[ChangeRequest]:
    """承認待ちのまま止まっている変更要求。"""

    threshold = now - timedelta(days=STALE_APPROVAL_DAYS)

    return (
        ChangeRequest.objects.select_related("project")
        .filter(
            project__tenant=tenant,
            project__deleted_at__isnull=True,
            status=ChangeRequest.Status.PENDING_APPROVAL,
            decided_at__isnull=True,
            created_at__lte=threshold,
        )
        .order_by("created_at")
    )


def overdue_tasks(tenant, *, today: date, min_overdue_days: int) -> QuerySet[WbsTask]:
    """期限を過ぎたまま終わっていないタスク。

    完了・アーカイブは除く。実績終了日が入っているものも、期限内に
    終わったかどうかは別問題なので、ここでは「まだ終わっていない」だけを見る。
    """

    limit = today - timedelta(days=min_overdue_days)

    return (
        WbsTask.objects.select_related("project")
        .filter(
            project__tenant=tenant,
            project__deleted_at__isnull=True,
            planned_end__isnull=False,
            planned_end__lte=limit,
            actual_end__isnull=True,
        )
        .exclude(status__in=[WbsTask.Status.DONE, WbsTask.Status.ARCHIVED])
        .order_by("planned_end")
    )


def sent_dedupe_keys(connection: Connection, keys: tuple[str, ...]) -> set[str]:
    """その接続で既に送信済みの抑止キー。

    `SENT` だけを見る。失敗・未送信（資格情報なし）は次回に再挑戦させたいので、
    抑止の材料にしない。
    """

    if not keys:
        return set()

    return set(
        NotificationLog.objects.filter(
            connection=connection,
            status=NotificationLog.Status.SENT,
        )
        .filter(Q(trigger__in=keys))
        .values_list("trigger", flat=True)
    )
