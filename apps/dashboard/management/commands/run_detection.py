"""実データを検査してアラートと介入提案を作る。

    python manage.py run_detection --tenant acme
    python manage.py run_detection --tenant acme --project p-001 --dry-run

`--dry-run` は保存せず、何を検知し何を見送るかだけを表示する。
しきい値を変えたときの影響を、本番データを汚さずに確かめられる。
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Tenant
from apps.dashboard.services.detection import run_detection
from apps.projects.models import Project


class Command(BaseCommand):
    help = "WBS・変更要求・不具合を検査し、アラートと介入提案を生成する"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument("--project", default="", help="案件コード（省略時はテナント内の全案件）")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="保存せず、検知内容と見送り理由だけを表示する",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        code = options["tenant"]

        try:
            tenant = Tenant.objects.get(code=code)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"テナントが見つかりません: {code}") from exc

        projects = Project.objects.alive().filter(tenant=tenant)

        if options["project"]:
            projects = projects.filter(code=options["project"])

            if not projects.exists():
                raise CommandError(f"案件が見つかりません: {options['project']}")

        result = run_detection(projects, dry_run=options["dry_run"])

        for finding in result.findings:
            self.stdout.write(
                self.style.WARNING(f"[検知] {finding.project.code} {finding.title}")
            )
            self.stdout.write(f"       根拠: {finding.reason}")

        for skip in result.skips:
            self.stdout.write(
                f"[見送] {skip.project.code} {skip.kind} / {skip.reason_label}: {skip.detail}"
            )

        self.stdout.write(self.style.SUCCESS(result.summary_line()))
