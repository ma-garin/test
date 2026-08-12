"""auto_running の Work Item から、実行可能な internal_apply Step だけを処理する。

    python manage.py process_pmo_work --tenant acme --limit 50

承認・外部反映は一切実行しない（`executor.execute_step` が observe/internal_apply
以外の Step を拒否するため、approve レベルの Step はここでは進まない）。
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.pmo_automation.models import PmoWorkItem, WorkItemState, WorkStepState
from apps.pmo_automation.services import executor


def _noop_internal_action() -> None:
    """P0 の安全なプレースホルダ。実際の下書き生成・再計算ロジックは別チケットで接続する。"""

    return None


class Command(BaseCommand):
    help = "auto_running の Work Item の internal_apply Step を安全に処理する"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument(
            "--limit", type=int, required=True, help="処理する Work Item の上限件数（必須）"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["tenant"]
        limit = options["limit"]

        if limit <= 0:
            raise CommandError("--limit は1以上を指定してください。")

        try:
            tenant = Tenant.objects.get(code=code)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"テナントが見つかりません: {code}") from exc

        now = timezone.now()
        work_items = PmoWorkItem.objects.filter(
            tenant=tenant, state=WorkItemState.AUTO_RUNNING
        ).order_by("created_at")[:limit]

        processed_work_items = 0
        for work_item in work_items:
            plan = work_item.plans.order_by("-version").first()
            if plan is None:
                continue

            for step in plan.steps.filter(
                state__in=[WorkStepState.PENDING, WorkStepState.RETRY_SCHEDULED]
            ).order_by("order"):
                if (
                    step.state == WorkStepState.RETRY_SCHEDULED
                    and step.next_retry_at is not None
                    and step.next_retry_at > now
                ):
                    continue

                try:
                    attempt = executor.execute_step(step, action=_noop_internal_action, now=now)
                except executor.StepNotExecutableError as error:
                    self.stdout.write(
                        self.style.WARNING(
                            f"[skip] {work_item.dedupe_key} step#{step.order}: {error}"
                        )
                    )
                    continue

                if attempt is not None:
                    self.stdout.write(
                        f"[{attempt.outcome}] {work_item.dedupe_key} step#{step.order}"
                    )

            processed_work_items += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{processed_work_items} 件の Work Item を処理しました（上限 {limit} 件）。"
            )
        )
