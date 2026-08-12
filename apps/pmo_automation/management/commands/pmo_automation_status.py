"""状態別件数・保留理由・承認待ちを機械可読/人可読で返す（読み取り専用）。

    python manage.py pmo_automation_status --tenant acme
    python manage.py pmo_automation_status --tenant acme --format json

DB・監査・外部・ファイルへは一切書き込まない。
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_automation.models import ApprovalRequest, ApprovalStatus, PmoWorkItem, WorkItemState


class Command(BaseCommand):
    help = "PMO Work Item の状態別件数・保留理由・承認待ちを表示する（読み取り専用）"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument("--format", choices=["text", "json"], default="text")

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["tenant"]
        output_format = options["format"]

        try:
            tenant = Tenant.objects.get(code=code)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"テナントが見つかりません: {code}") from exc

        rows = (
            PmoWorkItem.objects.filter(tenant=tenant)
            .values("state")
            .annotate(count=Count("id"))
            .order_by("state")
        )
        state_counts = {row["state"]: row["count"] for row in rows}

        hold_items = list(
            PmoWorkItem.objects.filter(tenant=tenant, state=WorkItemState.HOLD).values(
                "id", "kind", "block_reason"
            )
        )
        pending_approvals = ApprovalRequest.objects.filter(
            work_item__tenant=tenant, status=ApprovalStatus.PENDING
        ).count()

        payload = {
            "tenant": tenant.code,
            "state_counts": state_counts,
            "hold_reasons": hold_items,
            "pending_approvals": pending_approvals,
            "generated_at": timezone.now().isoformat(),
        }

        if output_format == "json":
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
            return

        self.stdout.write(f"テナント: {tenant.code}")
        for state, count in sorted(state_counts.items()):
            self.stdout.write(f"  {state}: {count}")
        self.stdout.write(f"承認待ち: {pending_approvals} 件")
        if hold_items:
            self.stdout.write("保留理由:")
            for row in hold_items:
                self.stdout.write(f"  - [{row['kind']}] {row['block_reason'] or '(理由未記録)'}")
