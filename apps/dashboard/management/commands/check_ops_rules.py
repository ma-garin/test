"""入力標準ルールの違反を検査し、担当者別に催促対象を出力する。

    python manage.py check_ops_rules --tenant acme
    python manage.py check_ops_rules --tenant acme --project p-001
    python manage.py check_ops_rules --tenant acme --assignee 佐藤

週次の締め前にこれを流し、出力をそのまま催促に使えることを狙っている。
保存は行わない（判定は毎回その時点のデータで作り直す）。
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Tenant
from apps.dashboard.selectors import ops_rule_tasks_for
from apps.dashboard.services.ops_rules import RULE_LABELS, build_ops_rules_report
from apps.projects.models import Project


class Command(BaseCommand):
    help = "WBS の入力標準ルール違反を検査し、担当者別に一覧表示する"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", required=True, help="テナントコード")
        parser.add_argument("--project", default="", help="案件コード（省略時はテナント内の全案件）")
        parser.add_argument("--assignee", default="", help="催促先で絞り込む（部分一致）")

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

        report = build_ops_rules_report(ops_rule_tasks_for(projects))

        self.stdout.write(
            f"判定日: {report.checked_on:%Y-%m-%d} / "
            f"更新基準日: {report.update_cutoff:%Y-%m-%d}"
        )
        self.stdout.write(f"対象タスク: {report.target_tasks}件")

        if not report.has_targets:
            # 0 件を「全員が守っている」と書かない。測れていないことを明示する。
            self.stdout.write(self.style.WARNING("対象なし: 判定できるタスクがありません"))
            return

        self._write_rules(report)
        self._write_assignees(report, keyword=options["assignee"])

    def _write_rules(self, report) -> None:
        self.stdout.write("")
        self.stdout.write("[ルール別]")

        for summary in report.rule_summaries:
            line = f"  {summary.label}: {summary.count}件 ({summary.description})"

            self.stdout.write(self.style.WARNING(line) if summary.count else line)

        disabled = [
            RULE_LABELS[rule][0] for rule in RULE_LABELS if rule not in report.enabled_rules
        ]

        if disabled:
            self.stdout.write(f"  ※無効なルール: {', '.join(disabled)}")

    def _write_assignees(self, report, *, keyword: str) -> None:
        self.stdout.write("")
        self.stdout.write("[担当者別]")

        summaries = [
            summary
            for summary in report.assignees
            if not keyword or keyword in summary.assignee
        ]

        if not summaries:
            message = "該当なし" if keyword else "違反なし: 対象タスクはすべてルールを満たしています"
            self.stdout.write(self.style.SUCCESS(message))
            return

        for summary in summaries:
            self.stdout.write(
                self.style.WARNING(
                    f"  {summary.assignee}: {summary.total}件 / タスク{summary.task_count}件"
                )
            )

            for violation in summary.violations:
                task = violation.task
                self.stdout.write(
                    f"    - [{violation.label}] {task.project.code} {task.wbs_code} {task.name}"
                )
                self.stdout.write(f"      根拠: {violation.detail}")

        self.stdout.write("")
        self.stdout.write(report.status_note)
