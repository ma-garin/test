"""既存イベントから Work Item を作り、決定的な Plan を評価まで進める。

    python manage.py run_pmo_automation --tenant acme --limit 50
    python manage.py run_pmo_automation --tenant acme --limit 50 --dry-run
    python manage.py run_pmo_automation --tenant acme --limit 50 --kind detection_triage

`--dry-run` は intake → assessing → planned → 評価 までを実際に実行し、
その結果をトランザクションごとロールバックすることで DB・監査・外部・
ファイルを一切変更しない（H-11: 保存せず実行計画だけを返す）。
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.dashboard.models import Alert
from apps.pmo_automation.models import WorkItemState, WorkKind
from apps.pmo_automation.services import intake, planning, workflow


class Command(BaseCommand):
    help = "既存イベント（Alert等）から PMO Work Item の intake・計画・評価を行う"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument(
            "--limit", type=int, required=True, help="1回で処理する Alert 件数の上限（無制限処理を避けるため必須）"
        )
        parser.add_argument(
            "--kind",
            choices=WorkKind.values,
            default="",
            help="この kind の Work Item だけを対象にする（省略時は全種別）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DB・監査・外部・ファイルを一切変更せず、実行計画だけを表示する",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["tenant"]
        limit = options["limit"]
        dry_run = options["dry_run"]
        kind_filter = options["kind"]

        if limit <= 0:
            raise CommandError("--limit は1以上を指定してください。")

        try:
            tenant = Tenant.objects.get(code=code)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"テナントが見つかりません: {code}") from exc

        plan_summaries: list[str] = []

        with transaction.atomic():
            alerts = Alert.objects.filter(project__tenant=tenant).order_by("id")[:limit]

            for alert in alerts:
                intake_result = intake.intake_from_alert(alert, dry_run=False)
                if not intake_result.created:
                    continue

                work_item = intake_result.work_item
                if kind_filter and work_item.kind != kind_filter:
                    # このAlertはintakeの時点でWork Itemができてしまうため、
                    # --kind の対象外なら取り消す（WorkLinkはCASCADEで一緒に消える）。
                    work_item.delete()
                    continue

                workflow.transition_work_item(work_item, WorkItemState.ASSESSING)

                now = timezone.now()
                evidence = planning.record_evidence(
                    work_item,
                    source_type="alert",
                    source_ref=str(alert.pk),
                    scope={"tenant": tenant.code, "project": alert.project.code},
                    content_hash=hashlib.sha256(
                        f"{alert.category}:{alert.title}:{alert.detail}".encode()
                    ).hexdigest(),
                    captured_at=now,
                )
                plan = planning.create_plan_and_evaluate(
                    work_item, evidence_bundles=[evidence], now=now
                )

                plan_summaries.append(
                    f"[{work_item.kind}] {work_item.dedupe_key} → "
                    f"state={work_item.state}, plan v{plan.version}, "
                    f"steps={plan.steps.count()}"
                )

            if dry_run:
                transaction.set_rollback(True)

        if not plan_summaries:
            self.stdout.write("対象イベントはありませんでした。")
            return

        prefix = "[dry-run] " if dry_run else ""
        for summary in plan_summaries:
            self.stdout.write(f"{prefix}{summary}")

        self.stdout.write(
            self.style.SUCCESS(f"{prefix}{len(plan_summaries)} 件の Work Item を処理しました。")
        )
