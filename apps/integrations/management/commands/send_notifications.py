"""通知ルールに沿って、未通知のものだけを送る。

    python manage.py send_notifications --tenant acme

既定はモックモードの接続でも動く（実送信せず履歴だけ残る）ので、
鍵を用意する前に「何が誰に飛ぶか」を確認できる。
`--dry-run` を付ければ履歴も残さず、送信予定だけを表示する。
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Tenant
from apps.integrations.notification_selectors import notify_connections
from apps.integrations.services.notify import (
    collect_notifications,
    send_pending_notifications,
)


class Command(BaseCommand):
    help = "重大アラート・介入提案・承認待ち滞留・期限超過タスクを Slack / Teams へ通知する"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="送信も履歴の記録も行わず、送信予定だけを表示する",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["tenant"]
        dry_run: bool = options["dry_run"]

        try:
            tenant = Tenant.objects.get(code=code)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"テナントが見つかりません: {code}") from exc

        connections = notify_connections(tenant)

        if not connections:
            # 「0 件送信」と「送り先が無い」は原因が違うので、明示的に伝える。
            self.stdout.write(
                self.style.WARNING(
                    f"{tenant.code}: 有効な通知先（Slack / Teams）が登録されていません"
                )
            )
            return

        if dry_run:
            self._preview(tenant)

        summary = send_pending_notifications(tenant, dry_run=dry_run, connections=connections)

        self.stdout.write(
            f"{tenant.code}: 通知先 {summary.connections} / 候補 {summary.candidates} / "
            f"送信 {summary.sent} / 抑止 {summary.suppressed} / 失敗 {summary.failed}"
        )

        if summary.failed:
            self.stdout.write(
                self.style.ERROR("失敗があります。NotificationLog の error を確認してください")
            )

    def _preview(self, tenant: Tenant) -> None:
        for note in collect_notifications(tenant):
            self.stdout.write(f"[{note.kind}] {note.title(len(note.keys))}")
